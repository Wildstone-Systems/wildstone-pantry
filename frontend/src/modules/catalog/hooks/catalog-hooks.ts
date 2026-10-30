import { useDebouncedValue } from "@mantine/hooks";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "@/api/client";

export type NutritionReference = {
  status: "verified" | "human_selected" | "label_exact" | "legacy_unknown" | "unverified" | "manual";
  source: string | null;
  fdc_id: number | null;
  description: string | null;
  why: string;
};

export type ReferenceReview = Omit<NutritionReference, "status"> & {
  status: NutritionReference["status"] | "rejected" | "unavailable";
};

export type Item = {
  id: string;
  name: string;
  canonical_ingredient?: string | null;
  canonical_provenance?: "generated" | "confirmed" | "legacy_unknown";
  canonical_version?: number | null;
  canonical_revision?: number;
  canonical_confirmed_at?: string | null;
  brand: string | null;
  barcode: string | null;
  image_url: string | null;
  default_expiration_days: number | null;
  // Package size the product is sold in (from OpenFoodFacts on scan, editable).
  size_quantity: number | null;
  size_unit: string | null;
  size_description: string | null;
  // How on-hand is tracked (ADR 0014): whole units vs by amount.
  stock_mode: "counted" | "measured";
  /** Base unit on-hand is stored & consumed in (ADR 0016): each / g / ml. */
  base_unit: string | null;
  restock_threshold: number | null;
  calories_per_unit: number | null;
  // Structured per-100g nutrition (from OpenFoodFacts on scan, or hand-entered).
  energy_kcal_100g: number | null;
  protein_g_100g: number | null;
  fat_g_100g: number | null;
  carbs_g_100g: number | null;
  serving_size_g: number | null;
  // Unit-conversion hints used to turn recipe/plate amounts into grams.
  density_g_per_ml: number | null;
  grams_per_each: number | null;
  // Raw provider nutrition blob (kept for reference alongside the structured fields).
  nutrition: Record<string, string | number | null> | null;
  // True when the macros are GENERIC values matched by name (no label nutrition
  // existed for this UPC) — shown as "Estimated" so they're never mistaken for exact.
  nutrition_estimated: boolean;
  /** When estimated, the USDA food the macros were matched to (description + FDC id). */
  nutrition_source: string | null;
  nutrition_reference?: NutritionReference;
  is_active: boolean;
  created_at: string;
};

export type ItemListResponse = { items: Item[] };

export function useItems(includeInactive = false) {
  return useQuery({
    queryKey: ["items", includeInactive],
    queryFn: async () => {
      const res = await api.get<ItemListResponse>("/api/items", {
        params: includeInactive ? { include_inactive: true } : undefined,
      });
      return res.data.items;
    },
  });
}

/** Live name/brand search — the scan station's no-scanner-handy typeahead.
 * `inStockOnly` narrows to items with a positive balance somewhere (nothing to
 * consume otherwise); omit it for the "in" direction, which restocks from the
 * whole catalog. Debounced 300ms and disabled below 2 characters so a
 * scanner-wedge typing a real barcode doesn't fire a search per keystroke. */
export function useItemSearch(search: string, opts: { inStockOnly?: boolean } = {}) {
  const [debounced] = useDebouncedValue(search.trim(), 300);
  return useQuery({
    queryKey: ["item-search", debounced, opts.inStockOnly ?? false],
    queryFn: async () => {
      const res = await api.get<ItemListResponse>("/api/items", {
        params: { search: debounced, ...(opts.inStockOnly ? { in_stock_only: true } : {}) },
      });
      return res.data.items;
    },
    enabled: debounced.length >= 2,
    staleTime: 10_000,
  });
}

export type ItemUpdateInput = {
  canonical_ingredient?: string;
  confirm_identity_change?: boolean;
  expected_canonical_ingredient?: string | null;
  expected_canonical_revision?: number;
  name: string;
  brand: string | null;
  barcode: string | null;
  size_quantity: number | null;
  size_unit: string | null;
  size_description: string | null;
  stock_mode?: "counted" | "measured";
  restock_threshold: number | null;
  calories_per_unit: number | null;
  energy_kcal_100g: number | null;
  protein_g_100g: number | null;
  fat_g_100g: number | null;
  carbs_g_100g: number | null;
  serving_size_g: number | null;
  density_g_per_ml: number | null;
  grams_per_each: number | null;
};

export function useUpdateItem() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async ({ id, ...patch }: ItemUpdateInput & { id: string }) => {
      const res = await api.put<Item>(`/api/items/${id}`, patch);
      return res.data;
    },
    onSuccess: async () => {
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["items"] }),
        queryClient.invalidateQueries({ queryKey: ["recipeAvailability"] }),
        queryClient.invalidateQueries({ queryKey: ["recipeSuggestions"] }),
        queryClient.invalidateQueries({ queryKey: ["ingredientMatches"] }),
      ]);
    },
  });
}

/** Archive (is_active=false) or reactivate (true) an item — soft delete. */
export function useSetItemActive() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async ({ id, is_active }: { id: string; is_active: boolean }) => {
      const res = await api.put<Item>(`/api/items/${id}`, { is_active });
      return res.data;
    },
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["items"] }),
  });
}

/** What purging an item would destroy — fetched to populate the confirmation. */
export type ItemUsage = {
  item_id: string;
  name: string;
  is_active: boolean;
  ledger_events: number;
  lots: number;
  on_hand: number;
  recipe_ingredients: number;
};

export function useItemUsage(itemId: string | null) {
  return useQuery({
    queryKey: ["item-usage", itemId],
    queryFn: async () => (await api.get<ItemUsage>(`/api/items/${itemId}/usage`)).data,
    enabled: Boolean(itemId),
  });
}

/** Permanently delete an item and its history. Irreversible.
 *
 * `confirmName` must exactly match the item's name — the server rejects
 * anything else, so this can't fire on a stray click.
 */
export function usePurgeItem() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async ({ id, confirmName }: { id: string; confirmName: string }) => {
      const res = await api.delete(`/api/items/${id}/purge`, {
        params: { confirm_name: confirmName },
      });
      return res.data;
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["items"] });
      queryClient.invalidateQueries({ queryKey: ["inventory"] });
    },
  });
}

export type ItemRescanResult = {
  reference_review?: ReferenceReview | null;
  item_id: string;
  status: "updated" | "no_change" | "not_found" | "no_barcode" | "rate_limited";
  filled: string[];
  /** True when the filled nutrition is a generic USDA name match, not label data. */
  estimated: boolean;
  /** The matched USDA food (description + FDC id) when estimated. */
  estimated_source: string | null;
  item: Item | null;
};

/** Re-query one item against the external provider, filling any missing fields. */
export function useRescanItem() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (itemId: string) => {
      const res = await api.post<ItemRescanResult>(`/api/items/${itemId}/rescan`);
      return res.data;
    },
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["items"] }),
  });
}

/** Rescan a raw item id without the mutation wrapper — for the bulk loop. */
export async function rescanItemById(itemId: string): Promise<ItemRescanResult> {
  const res = await api.post<ItemRescanResult>(`/api/items/${itemId}/rescan`);
  return res.data;
}

export type NutritionCandidate = {
  source?: "usda_fdc";
  verification_status?: "unverified";
  fdc_id: number | null;
  description: string | null;
  data_type: string | null;
  calories: number | null;
  protein_g: number | null;
  fat_g: number | null;
  carbs_g: number | null;
};

/** USDA foods to choose from for an item's nutrition (the picker). Fetched on
 *  demand — only when the picker is open. `query` refines the USDA search term. */
export function useNutritionCandidates(itemId: string | null, enabled: boolean, query?: string) {
  const q = (query ?? "").trim();
  return useQuery({
    queryKey: ["nutritionCandidates", itemId, q],
    enabled: Boolean(itemId) && enabled,
    queryFn: async () => {
      const res = await api.get<{ item_id: string; candidates: NutritionCandidate[]; throttled: boolean }>(
        `/api/items/${itemId}/nutrition-candidates`,
        { params: q ? { q } : undefined }
      );
      return res.data;
    },
    staleTime: 60_000,
    // While USDA is rate-limiting, keep retrying every few seconds so the list
    // fills itself once the throttle clears — no manual re-search needed.
    refetchInterval: (query) => (query.state.data?.throttled ? 4_000 : false),
  });
}

/** USDA foods matching `query`, with no item to scope the search to — the picker
 *  for a brand-new item that doesn't exist yet (Add Item's "Match to a USDA
 *  food…"). Same candidate shape as `useNutritionCandidates`, just item-agnostic. */
export function useSearchNutrition(query: string, enabled: boolean) {
  const q = query.trim();
  return useQuery({
    queryKey: ["searchNutrition", q],
    enabled: enabled && q.length > 0,
    queryFn: async () => {
      const res = await api.get<{ throttled: boolean; searched_term: string; candidates: NutritionCandidate[] }>(
        "/api/items/search-nutrition",
        { params: { q } }
      );
      return res.data;
    },
    staleTime: 60_000,
    refetchInterval: (query) => (query.state.data?.throttled ? 4_000 : false),
  });
}

/** Apply a chosen USDA food's macros to an item; returns the updated item. The
 *  `query` (the search the food was picked from) lets the server reproduce the
 *  same result list and match the food by FDC id. */
export function useApplyUsdaNutrition() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async ({ itemId, fdcId, query }: { itemId: string; fdcId: number; query?: string }) => {
      const res = await api.post<Item>(`/api/items/${itemId}/nutrition-from-usda`, {
        fdc_id: fdcId,
        ...(query?.trim() ? { q: query.trim() } : {}),
      });
      return res.data;
    },
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["items"] }),
  });
}

export type Location = {
  id: string;
  name: string;
  description: string | null;
  is_active: boolean;
  created_at: string;
};

export type LocationListResponse = { locations: Location[] };

export function useLocations(includeInactive = false) {
  return useQuery({
    queryKey: ["locations", includeInactive],
    queryFn: async () => {
      const res = await api.get<LocationListResponse>("/api/locations", {
        params: includeInactive ? { include_inactive: true } : undefined,
      });
      return res.data.locations;
    },
  });
}

export type LocationUpdateInput = {
  name: string;
  description: string | null;
};

export function useUpdateLocation() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async ({ id, ...patch }: LocationUpdateInput & { id: string }) => {
      const res = await api.put<Location>(`/api/locations/${id}`, patch);
      return res.data;
    },
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["locations"] }),
  });
}

/** Archive (is_active=false) or reactivate (true) a location — soft delete. */
export function useSetLocationActive() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async ({ id, is_active }: { id: string; is_active: boolean }) => {
      const res = await api.put<Location>(`/api/locations/${id}`, { is_active });
      return res.data;
    },
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["locations"] }),
  });
}

/** One stored item whose own numbers disagree with themselves. */
export type DataQualityRow = {
  item_id: string;
  name: string;
  brand: string | null;
  estimated: boolean;
  source: string | null;
  energy_kcal_100g: number | null;
  protein_g_100g: number | null;
  fat_g_100g: number | null;
  carbs_g_100g: number | null;
  issues: { severity: "impossible" | "suspect"; field: string; message: string }[];
};

export function useDataQuality() {
  return useQuery({
    queryKey: ["data-quality"],
    queryFn: async () =>
      (await api.get<{ items: DataQualityRow[]; impossible: number; suspect: number }>("/api/items/data-quality")).data,
  });
}
