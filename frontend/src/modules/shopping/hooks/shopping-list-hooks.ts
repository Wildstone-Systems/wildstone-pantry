import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api } from "@/api/client";
import type { Offer, ShoppingListEntry } from "@/api/types";

const KEY = ["shopping-list"] as const;

export type OfferSearchResult = { query: string; offers: Offer[]; throttled: string[] };

/** Search integrations for purchasable offers matching a need (ADR 0013). */
export function useOfferSearch(query: string) {
  const q = query.trim();
  return useQuery({
    queryKey: ["offer-search", q],
    enabled: q.length >= 2,
    queryFn: async () => {
      const res = await api.get<OfferSearchResult>("/api/shopping-list/search", { params: { q } });
      return res.data;
    },
    staleTime: 60_000,
  });
}

/** Resolve an offer → create/link catalog item + add (or resolve) a list entry. */
export function useAddFromOffer() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (
      input: Offer & { quantity?: number; unit?: string; entry_id?: string; item_id?: string | null },
    ) => {
      const res = await api.post<ShoppingListEntry>("/api/shopping-list/from-offer", input);
      return res.data;
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: KEY });
      qc.invalidateQueries({ queryKey: ["items"] });
    },
  });
}

export function useShoppingList() {
  return useQuery({
    queryKey: KEY,
    queryFn: async () => {
      const res = await api.get<{ items: ShoppingListEntry[] }>("/api/shopping-list");
      return res.data.items;
    },
  });
}

export function useAddShoppingItem() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (input: { name: string; quantity?: number; unit?: string }) => {
      const res = await api.post<ShoppingListEntry>("/api/shopping-list", input);
      return res.data;
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: KEY }),
  });
}

export function useUpdateShoppingItem() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async ({ id, ...patch }: { id: string; quantity?: number; status?: "pending" | "purchased" }) => {
      const res = await api.patch<ShoppingListEntry>(`/api/shopping-list/${id}`, patch);
      return res.data;
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: KEY }),
  });
}

export function useDeleteShoppingItem() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (id: string) => {
      await api.delete(`/api/shopping-list/${id}`);
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: KEY }),
  });
}

export type ExportTarget = { key: string; label: string; configured: boolean };
export type ExportResult = { target: string; format: string; content_type: string; body: string; filename: string | null };

export function useExportTargets() {
  return useQuery({
    queryKey: ["shopping-list", "export-targets"],
    queryFn: async () => {
      const res = await api.get<{ targets: ExportTarget[] }>("/api/shopping-list/export/targets");
      return res.data.targets;
    },
  });
}

export async function fetchExport(target: string): Promise<ExportResult> {
  const res = await api.get<ExportResult>("/api/shopping-list/export", { params: { target } });
  return res.data;
}

export type KrogerCartResult = {
  added: { upc: string; quantity: number; name: string }[];
  skipped: { name: string; reason: string }[];
  failed: { name: string; reason: string }[];
  cart_url: string;
};

/** Push the pending buy list into the connected user's Kroger cart (Phase 4). */
export async function pushToKrogerCart(): Promise<KrogerCartResult> {
  const res = await api.post<KrogerCartResult>("/api/shopping-list/kroger-cart");
  return res.data;
}

export type InstacartResult = { url: string; count: number };

/** Turn the pending list into an Instacart shoppable-list link (Phase 4b, dormant). */
export async function createInstacartLink(): Promise<InstacartResult> {
  const res = await api.post<InstacartResult>("/api/shopping-list/instacart");
  return res.data;
}

export type PurchaseConnector = { key: string; label: string; configured: boolean; configurable: boolean };

export function usePurchaseConnectors() {
  return useQuery({
    queryKey: ["purchase-connectors"],
    queryFn: async () => {
      const res = await api.get<{ connectors: PurchaseConnector[] }>("/api/purchases/connectors");
      return res.data.connectors;
    },
  });
}

export type NormalizedOrderLine = {
  name: string;
  quantity: string;
  unit: string;
  upc: string | null;
  unit_price: string | null;
};
export type NormalizedOrder = {
  vendor: string;
  purchased_on: string | null;
  external_id: string | null;
  lines: NormalizedOrderLine[];
};

export async function previewConnector(key: string): Promise<NormalizedOrder[]> {
  const res = await api.post<{ connector: string; orders: NormalizedOrder[] }>(
    `/api/purchases/connectors/${key}/preview`,
  );
  return res.data.orders;
}

export type ImportConnectorResult = {
  connector: string;
  orders: number;
  imported_orders: number;
  skipped_orders: number;
  imported_lines: number;
  transaction_ids: string[];
};

export function useImportConnector() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async ({ key, locationId }: { key: string; locationId: string }) => {
      const res = await api.post<ImportConnectorResult>(`/api/purchases/connectors/${key}/import`, {
        location_id: locationId,
      });
      return res.data;
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["inventory"] });
      qc.invalidateQueries({ queryKey: KEY });
    },
  });
}
