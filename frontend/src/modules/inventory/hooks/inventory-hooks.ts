import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "@/api/client";

function toQueryString(params: Record<string, string | number | boolean | undefined | null>) {
  const query = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value === undefined || value === null || value === "") continue;
    query.set(key, String(value));
  }
  const encoded = query.toString();
  return encoded ? `?${encoded}` : "";
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

export type LocationCandidate = { location_id: string; location_name: string; quantity: string };

/** Where an item should land for a location-less pick — the same auto-find
 * used by the scan pages, exposed for any other "which location" flow (log a
 * meal, etc). `direction: "out"` = where it's actually in stock (empty if
 * nowhere); `"in"` = where it already lives, or every location if it's never
 * been stocked. One candidate means the caller can use it with no prompt. */
export function useItemLocations(itemId: string | null, direction: "in" | "out") {
  return useQuery({
    queryKey: ["item-locations", itemId, direction],
    enabled: Boolean(itemId),
    queryFn: async () => {
      const res = await api.get<LocationCandidate[]>("/api/inventory/item-locations", {
        params: { item_id: itemId, direction },
      });
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

export type Container = { id: string; location_id: string; name: string; description: string | null; created_at: string };

export function useContainers(locationId: string | null) {
  return useQuery({
    queryKey: ["containers", locationId],
    enabled: Boolean(locationId),
    queryFn: async () => {
      const res = await api.get<{ containers: Container[] }>(
        `/api/inventory/containers${toQueryString({ location_id: locationId ?? undefined })}`,
      );
      return res.data.containers;
    },
  });
}

export function useCreateContainer() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (input: { location_id: string; name: string; description?: string | null }) => {
      const res = await api.post<Container>("/api/inventory/containers", input);
      return res.data;
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: ["containers"] }),
  });
}

export function useDeleteContainer() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (id: string) => {
      await api.delete(`/api/inventory/containers/${id}`);
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: ["containers"] }),
  });
}

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
