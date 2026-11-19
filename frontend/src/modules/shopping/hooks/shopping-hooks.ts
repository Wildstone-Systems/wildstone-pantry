/** Shopping domain hooks - purchase history, barcode lookup. */

import { useQuery, useMutation } from "@tanstack/react-query";
import { api } from "@/api/client";

// ============ Types ============

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

export type PurchaseDetailResponse = {
  purchase: PurchaseRead;
  lines: PurchaseLine[];
};

export type BarcodeLookupApiResponse = {
  barcode: string;
  found: boolean;
  item_id: string | null;
  item_name: string | null;
  source: string | null;
};

// ============ Purchase Queries ============

export function usePurchases(limit = 25) {
  return useQuery({
    queryKey: ["purchases", limit],
    queryFn: async () => {
      const params = new URLSearchParams();
      if (limit) params.set("limit", String(limit));
      const res = await api.get<{ purchases: PurchaseRead[] }>(`/api/purchases?${params.toString()}`);
      return res.data.purchases;
    },
  });
}

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

// ============ Barcode ============

export function useBarcodeLookup() {
  return useMutation({
    mutationFn: async (barcode: string) => {
      const res = await api.get<BarcodeLookupApiResponse>(`/api/barcodes/lookup/${encodeURIComponent(barcode.trim())}`);
      return res.data;
    },
  });
}
