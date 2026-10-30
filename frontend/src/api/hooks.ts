import { useQuery } from "@tanstack/react-query";

import { api } from "./client";
import type { HealthResponse, HouseholdProfile } from "./types/platform";

// Canonical definitions live in api/types; re-exported for existing consumers.
export type { HealthResponse, HouseholdProfile };

// Household profiles list is OPEN (the kiosk shows people to pick). Shared here
// so both the kiosk shell and the admin Household page use one query + cache key.
export function useHouseholdProfiles(includeInactive = false) {
  return useQuery({
    queryKey: ["household", "profiles", { includeInactive }],
    queryFn: async () => {
      const res = await api.get<{ profiles: HouseholdProfile[] }>("/api/household/profiles", {
        params: includeInactive ? { include_inactive: true } : undefined,
      });
      return res.data.profiles;
    },
  });
}

function toQueryString(params: Record<string, string | number | boolean | undefined | null>) {
  const query = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value === undefined || value === null || value === "") continue;
    query.set(key, String(value));
  }
  const encoded = query.toString();
  return encoded ? `?${encoded}` : "";
}

export type SetupStatus = {
  completed: boolean;
  needs_admin: boolean;
  household_name: string | null;
  accounts_mode: boolean;
};

/** First-run wizard state (ADR 0018 §7). Never blocks the app: consumers must
 * treat loading/error as "assume set up" so a flaky backend can't strand the UI. */
export function useSetupStatus() {
  return useQuery({
    queryKey: ["setupStatus"],
    queryFn: async () => {
      const res = await api.get<SetupStatus>("/api/setup/status");
      return res.data;
    },
    staleTime: 60_000,
    retry: 1,
  });
}

export function useHealth(pollMs = 5000) {
  return useQuery({
    queryKey: ["health"],
    queryFn: async () => {
      const res = await api.get<HealthResponse>("/health");
      return res.data;
    },
    refetchInterval: pollMs,
  });
}

export type Item = {
  id: string;
  name: string;
  brand: string | null;
  barcode: string | null;
  default_expiration_days: number | null;
  created_at: string;
};

export type ItemListResponse = { items: Item[] };

export function useItems() {
  return useQuery({
    queryKey: ["items"],
    queryFn: async () => {
      const res = await api.get<ItemListResponse>("/api/items");
      return res.data.items;
    },
  });
}

export type Location = {
  id: string;
  name: string;
  description: string | null;
  created_at: string;
};

export type LocationListResponse = { locations: Location[] };

export function useLocations() {
  return useQuery({
    queryKey: ["locations"],
    queryFn: async () => {
      const res = await api.get<LocationListResponse>("/api/locations");
      return res.data.locations;
    },
  });
}

export type InventoryLine = {
  inventory_id: string;
  item_id: string;
  item_name: string;
  location_id: string;
  quantity: string | number;
  unit: string;
  expiration_date: string | null;
  updated_at: string;
};

export type InventoryResponse = { location_id: string; location_name: string; items: InventoryLine[] };

export function useInventory(locationId: string | null) {
  return useQuery({
    queryKey: ["inventory", locationId],
    enabled: Boolean(locationId),
    queryFn: async () => {
      const res = await api.get<InventoryResponse>(`/api/inventory${toQueryString({ location_id: locationId ?? undefined })}`);
      return res.data;
    },
  });
}

export type TransactionLine = {
  transaction_id: string;
  item_id: string;
  item_name: string;
  location_id: string;
  location_name: string;
  quantity_change: string | number;
  unit: string;
  transaction_type: string;
  reason: string | null;
  note: string | null;
  created_at: string;
};

export type TransactionResponse = { transactions: TransactionLine[] };

export function useTransactions(locationId: string | null, limit = 50) {
  return useQuery({
    queryKey: ["transactions", locationId, limit],
    enabled: Boolean(locationId),
    queryFn: async () => {
      const res = await api.get<TransactionResponse>(
        `/api/inventory/transactions${toQueryString({ location_id: locationId ?? undefined, limit })}`,
      );
      return res.data.transactions;
    },
  });
}

export type BarcodeLookupApiResponse = {
  barcode: string;
  found: boolean;
  item_id: string | null;
  item_name: string | null;
  source: string | null;
};

export type ReceiptLine = {
  line_item_id: string;
  raw_line: string;
  parsed_name: string | null;
  parsed_quantity: string | number;
  parsed_unit: string;
  matched_item_id: string | null;
  matched_item_name: string | null;
};

export type ReceiptIngestApiResponse = {
  receipt_id: string;
  status: string;
  line_count: number;
  inventory_transaction_ids: string[];
  lines: ReceiptLine[];
  created_at: string;
};

export type ReceiptPreviewLine = {
  raw_line: string;
  name: string;
  quantity: string;
  unit: string;
  matched_item_id: string | null;
  matched_item_name: string | null;
  expiration_days_estimate: number | null;
};

export type ReceiptPreviewApiResponse = {
  lines: ReceiptPreviewLine[];
  line_count: number;
};

export type ReceiptCommitLine = {
  raw_line: string;
  name: string;
  quantity: string;
  unit: string;
  matched_item_id: string | null;
  create_new: boolean;
};

export type ReceiptCommitApiResponse = ReceiptIngestApiResponse;

export type PurchaseLine = {
  line_item_id: string;
  raw_line: string;
  parsed_name: string | null;
  parsed_quantity: string | number;
  parsed_unit: string;
  parsed_barcode: string | null;
  matched_item_id: string | null;
  matched_item_name: string | null;
};

export type PurchaseImportApiResponse = {
  purchase_id: string;
  status: string;
  line_count: number;
  created_item_ids: string[];
  inventory_transaction_ids: string[];
  lines: PurchaseLine[];
  created_at: string;
};

export type PurchaseDisplayedLine = PurchaseLine & {
  would_create_item?: boolean;
  would_add_to_inventory?: boolean;
};

export type PurchasePreviewApiResponse = {
  fingerprint: string;
  duplicate_of_purchase_id: string | null;
  line_count: number;
  would_create_item_names: string[];
  lines: PurchaseDisplayedLine[];
};

export type PurchaseRead = {
  id: string;
  merchant: string | null;
  source: string;
  purchased_on: string | null;
  location_id: string | null;
  status: string;
  line_count: number;
  created_at: string;
};

export type PurchaseListResponse = { purchases: PurchaseRead[] };

export function usePurchases(limit = 25) {
  return useQuery({
    queryKey: ["purchases", limit],
    queryFn: async () => {
      const res = await api.get<PurchaseListResponse>(`/api/purchases${toQueryString({ limit })}`);
      return res.data.purchases;
    },
  });
}

export type PurchaseDetailResponse = { purchase: PurchaseRead; lines: PurchaseLine[] };

export function usePurchaseDetail(purchaseId: string | null) {
  return useQuery({
    queryKey: ["purchaseDetail", purchaseId],
    enabled: Boolean(purchaseId),
    queryFn: async () => {
      const res = await api.get<PurchaseDetailResponse>(`/api/purchases/${purchaseId}`);
      return res.data;
    },
  });
}

export type Recipe = {
  id: string;
  name: string;
  instructions: string | null;
  created_at: string;
};

export type RecipeIngredientInput = {
  item_id: string;
  quantity_required: number;
  unit: string;
};

export type RecipeWithIngredients = {
  recipe: Recipe;
  ingredients: RecipeIngredientInput[];
};

export function useRecipes() {
  return useQuery({
    queryKey: ["recipes"],
    queryFn: async () => {
      const res = await api.get<Recipe[]>("/api/recipes");
      return res.data;
    },
  });
}

export type RecipeSuggestion = {
  recipe_id: string;
  recipe_name: string;
  available_score: number;
  missing_items: string[];
};

export type RecipeSuggestionResponse = { location_id: string; suggestions: RecipeSuggestion[] };

export function useRecipeSuggestions(locationId: string | null) {
  return useQuery({
    queryKey: ["recipeSuggestions", locationId],
    enabled: Boolean(locationId),
    queryFn: async () => {
      const res = await api.get<RecipeSuggestionResponse>(`/api/recipes/suggestions/${locationId}`);
      return res.data.suggestions;
    },
  });
}

export type RecipeParsedIngredient = {
  raw_line: string;
  name: string;
  quantity: number;
  unit: string;
};

export type RecipeParseApiResponse = {
  title: string;
  instructions: string;
  ingredients: RecipeParsedIngredient[];
  raw_text: string;
};

export type ConsumptionTrend = {
  item_id: string;
  item_name: string;
  avg_daily_usage: number;
  days_to_depletion: number | null;
  sampled_days: number;
};

export type ConsumptionTrendsResponse = { location_id: string; trends: ConsumptionTrend[] };

export function useConsumptionTrends(locationId: string | null, lookbackDays = 30) {
  return useQuery({
    queryKey: ["consumptionTrends", locationId, lookbackDays],
    enabled: Boolean(locationId),
    queryFn: async () => {
      const res = await api.get<ConsumptionTrendsResponse>(
        `/api/insights/trends/${locationId}${toQueryString({ lookback_days: lookbackDays })}`,
      );
      return res.data.trends;
    },
  });
}

export type RestockSuggestion = {
  item_id: string;
  item_name: string;
  current_quantity: number;
  priority: number;
  reason: string;
  recommendation: string;
};

export type RestockResponse = { location_id: string; suggestions: RestockSuggestion[] };

export function useRestockSuggestions(locationId: string | null) {
  return useQuery({
    queryKey: ["restockSuggestions", locationId],
    enabled: Boolean(locationId),
    queryFn: async () => {
      const res = await api.get<RestockResponse>(`/api/insights/restock/${locationId}`);
      return res.data.suggestions;
    },
  });
}

export type ExpiringItem = {
  item_id: string;
  item_name: string;
  expiration_date: string;
  days_until_expiration: number;
};

export type ExpiringResponse = { location_id: string; expiring_items: ExpiringItem[] };

export function useExpiringItems(locationId: string | null, withinDays = 7) {
  return useQuery({
    queryKey: ["expiringItems", locationId, withinDays],
    enabled: Boolean(locationId),
    queryFn: async () => {
      const res = await api.get<ExpiringResponse>(
        `/api/insights/expiring/${locationId}${toQueryString({ within_days: withinDays })}`,
      );
      return res.data.expiring_items;
    },
  });
}

export type FeedbackRead = {
  id: string;
  user_id: string;
  category: string;
  route: string | null;
  message: string;
  status: string;
  created_at: string;
};

export type FeedbackListResponse = { feedback: FeedbackRead[] };

export function useFeedback(limit = 50, status: string | null = null) {
  return useQuery({
    queryKey: ["feedback", limit, status || "all"],
    queryFn: async () => {
      const res = await api.get<FeedbackListResponse>(`/api/feedback${toQueryString({ limit, status })}`);
      return res.data.feedback;
    },
  });
}
