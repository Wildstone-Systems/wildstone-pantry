import { useMutation, useQueries, useQueryClient } from "@tanstack/react-query";

import { api } from "@/api/client";
import type { ConsumptionTrend, ExpiringItem, RecipeSuggestion, RestockSuggestion } from "@/api/hooks";
import { useLocations, type Location } from "@/modules/catalog";

import type { Container, InventoryLine, TransactionLine } from "./inventory-hooks";

// Lots and waste still live on the backend (`/api/inventory/lots|waste`) but are
// not surfaced by inventory-hooks, so their row shapes are declared here.
export type LotLine = {
  lot_id: string;
  item_id: string;
  item_name: string;
  location_id: string;
  container_id: string | null;
  container_name: string | null;
  quantity_remaining: string | number;
  unit: string;
  expiration_date: string | null;
  unit_price: string | number | null;
  status: string;
  frozen: boolean;
  days_until_expiration: number | null;
};

export type WasteLine = {
  item_id: string;
  item_name: string;
  quantity_wasted: string | number;
  unit: string;
  events: number;
};

/**
 * Location-scoped data hooks.
 *
 * A `locationId` of `null` means "All locations": each hook fans out one request
 * per location (React Query `useQueries`) and merges the results, tagging every
 * row with `location_id` / `location_name` so the UI can show where each item
 * lives. A concrete `locationId` fetches just that location. Queries live under a
 * dedicated `["scoped", …]` key namespace so they never collide with the
 * single-location hooks used elsewhere (Insights, Recipes); writes refresh them
 * by invalidating `["scoped"]`.
 */

export type WithLocation<T> = T & { location_id: string; location_name: string };

export type ScopedResult<T> = {
  data: WithLocation<T>[];
  isLoading: boolean;
  isFetching: boolean;
  refetch: () => void;
};

function qs(params: Record<string, string | number | undefined>) {
  const query = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value === undefined || value === "") continue;
    query.set(key, String(value));
  }
  const encoded = query.toString();
  return encoded ? `?${encoded}` : "";
}

function useScoped<T>(
  type: string,
  locationId: string | null,
  fetchOne: (loc: Location) => Promise<T[]>,
  extraKey: Array<string | number> = [],
): ScopedResult<T> {
  const { data: locations = [], isLoading: locationsLoading } = useLocations();
  const targets = locationId ? locations.filter((loc) => loc.id === locationId) : locations;

  const results = useQueries({
    queries: targets.map((loc) => ({
      queryKey: ["scoped", type, loc.id, ...extraKey],
      queryFn: async (): Promise<WithLocation<T>[]> => {
        const rows = await fetchOne(loc);
        return rows.map((row) => ({ ...row, location_id: loc.id, location_name: loc.name }));
      },
    })),
  });

  return {
    data: results.flatMap((r) => r.data ?? []),
    isLoading: locationsLoading || results.some((r) => r.isLoading),
    isFetching: results.some((r) => r.isFetching),
    refetch: () => {
      results.forEach((r) => r.refetch());
    },
  };
}

export function useScopedInventory(locationId: string | null) {
  return useScoped<InventoryLine>("inventory", locationId, async (loc) => {
    const res = await api.get<{ items: InventoryLine[] }>(`/api/inventory${qs({ location_id: loc.id })}`);
    return res.data.items;
  });
}

export function useScopedLots(locationId: string | null) {
  return useScoped<LotLine>("lots", locationId, async (loc) => {
    const res = await api.get<{ lots: LotLine[] }>(`/api/inventory/lots${qs({ location_id: loc.id })}`);
    return res.data.lots;
  });
}

export function useScopedWaste(locationId: string | null) {
  return useScoped<WasteLine>("waste", locationId, async (loc) => {
    const res = await api.get<{ items: WasteLine[] }>(`/api/inventory/waste${qs({ location_id: loc.id })}`);
    return res.data.items;
  });
}

/**
 * A row on the tracking worklist (ADR 0023 D8). `source` says why it surfaced —
 * "expiring" has a deadline, "open" is a started container, "recent" is ordinary
 * usage — so the UI can label rows honestly rather than implying all are urgent.
 * `lot_id` is null only on a "recent" row, which names an item, not a container.
 */
export type WorklistRow = {
  source: "expiring" | "open" | "recent";
  item_id: string;
  item_name: string;
  location_id: string;
  lot_id: string | null;
  quantity_remaining: string | number;
  unit: string | null;
  expiration_date: string | null;
  days_until_expiration: number | null;
  status: string | null;
  sealed_count: number | null;
  container_size: string | number | null;
};

export function useScopedWorklist(locationId: string | null, withinDays = 7) {
  return useScoped<WorklistRow>(
    "worklist",
    locationId,
    async (loc) => {
      const res = await api.get<{ rows: WorklistRow[] }>(
        `/api/inventory/worklist${qs({ location_id: loc.id, within_days: withinDays })}`,
      );
      return res.data.rows;
    },
    [withinDays],
  );
}

export function useScopedContainers(locationId: string | null) {
  return useScoped<Container>("containers", locationId, async (loc) => {
    const res = await api.get<{ containers: Container[] }>(`/api/inventory/containers${qs({ location_id: loc.id })}`);
    return res.data.containers;
  });
}

export function useScopedTransactions(locationId: string | null, limit = 50) {
  return useScoped<TransactionLine>(
    "transactions",
    locationId,
    async (loc) => {
      const res = await api.get<{ transactions: TransactionLine[] }>(
        `/api/inventory/transactions${qs({ location_id: loc.id, limit })}`,
      );
      return res.data.transactions;
    },
    [limit],
  );
}

export function useScopedExpiring(locationId: string | null, withinDays = 7) {
  return useScoped<ExpiringItem>(
    "expiring",
    locationId,
    async (loc) => {
      const res = await api.get<{ expiring_items: ExpiringItem[] }>(
        `/api/insights/expiring/${loc.id}${qs({ within_days: withinDays })}`,
      );
      return res.data.expiring_items;
    },
    [withinDays],
  );
}

export function useScopedRestock(locationId: string | null) {
  return useScoped<RestockSuggestion>("restock", locationId, async (loc) => {
    const res = await api.get<{ suggestions: RestockSuggestion[] }>(`/api/insights/restock/${loc.id}`);
    return res.data.suggestions;
  });
}

export function useScopedTrends(locationId: string | null, lookbackDays = 30) {
  return useScoped<ConsumptionTrend>(
    "trends",
    locationId,
    async (loc) => {
      const res = await api.get<{ trends: ConsumptionTrend[] }>(
        `/api/insights/trends/${loc.id}${qs({ lookback_days: lookbackDays })}`,
      );
      return res.data.trends;
    },
    [lookbackDays],
  );
}

export type ReverseTransactionResult = { transaction_id: string; reversed_id: string };

/** Reverses a logged transaction by adding a compensating entry (reason:
 * "reversal") — history is never edited or deleted. 409s (already reversed, is
 * itself a reversal, insufficient remaining stock) surface via the caller's
 * onError; this hook just wires the request and refreshes the views the
 * reversal affects. */
export function useReverseTransaction() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (transactionId: string) => {
      const res = await api.post<ReverseTransactionResult>(`/api/inventory/transactions/${transactionId}/reverse`);
      return res.data;
    },
    onSuccess: () => {
      void Promise.all([
        queryClient.invalidateQueries({ queryKey: ["scoped", "transactions"] }),
        queryClient.invalidateQueries({ queryKey: ["scoped", "inventory"] }),
        // On Hand and Lots both derive from the lot projection — without this
        // they'd keep showing pre-reversal quantities while the Log refreshed.
        queryClient.invalidateQueries({ queryKey: ["scoped", "lots"] }),
        queryClient.invalidateQueries({ queryKey: ["transactions"] }),
        queryClient.invalidateQueries({ queryKey: ["inventory"] }),
        queryClient.invalidateQueries({ queryKey: ["lots"] }),
      ]);
    },
  });
}

export function useScopedRecipeSuggestions(locationId: string | null) {
  return useScoped<RecipeSuggestion>("recipeSuggestions", locationId, async (loc) => {
    const res = await api.get<{ suggestions: RecipeSuggestion[] }>(`/api/recipes/suggestions/${loc.id}`);
    return res.data.suggestions;
  });
}
