/** Shopping domain hooks.
 *
 * Self-contained shopping hooks module (Phase 5).
/** Shopping domain hooks - receipts, purchases, buy list (Phase 5). */

import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
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

export type PurchaseDisplayedLine = PurchaseLine & {
  would_create_item?: boolean;
  would_add_to_inventory?: boolean;
};

export type PurchaseDetailResponse = {
  purchase: PurchaseRead;
  lines: PurchaseLine[];
};

export type PurchasePreviewApiResponse = {
  fingerprint: string;
  duplicate_of_purchase_id: string | null;
  line_count: number;
  would_create_item_names: string[];
  lines: PurchaseDisplayedLine[];
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

// ============ Receipt/Purchase Mutations ============

export function usePreviewPurchaseCsv() {
  return useMutation({
    mutationFn: async (payload: {
      locationId: string;
      csvText: string;
      merchant: string | null;
      purchasedOn: string | null;
      createMissingItems: boolean;
      applyToInventory: boolean;
    }) => {
      const res = await api.post<PurchasePreviewApiResponse>("/api/purchases/preview-csv", {
        location_id: payload.locationId,
        csv_text: payload.csvText,
        merchant: payload.merchant,
        purchased_on: payload.purchasedOn,
        create_missing_items: payload.createMissingItems,
        apply_to_inventory: payload.applyToInventory,
        default_unit: "each",
      });
      return res.data;
    },
  });
}

export function useImportPurchaseCsv() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (payload: {
      locationId: string;
      csvText: string;
      merchant: string | null;
      purchasedOn: string | null;
      createMissingItems: boolean;
      applyToInventory: boolean;
    }) => {
      const res = await api.post<PurchaseImportApiResponse>("/api/purchases/import-csv", {
        location_id: payload.locationId,
        csv_text: payload.csvText,
        merchant: payload.merchant,
        purchased_on: payload.purchasedOn,
        create_missing_items: payload.createMissingItems,
        apply_to_inventory: payload.applyToInventory,
        default_unit: "each",
      });
      return res.data;
    },
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ["purchases"] });
      await queryClient.invalidateQueries({ queryKey: ["items"] });
      await queryClient.invalidateQueries({ queryKey: ["inventory"] });
      await queryClient.invalidateQueries({ queryKey: ["transactions"] });
    },
  });
}

export function useIngestReceiptImage() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (params: { locationId: string; file: File }) => {
      const form = new FormData();
      form.append("file", params.file);
      const res = await api.post<ReceiptIngestApiResponse>(`/api/receipts/ingest-image?location_id=${params.locationId}`, form, {
        headers: { "Content-Type": "multipart/form-data" },
      });
      return res.data;
    },
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ["inventory"] });
      await queryClient.invalidateQueries({ queryKey: ["transactions"] });
    },
  });
}

export function usePreviewReceipt() {
  return useMutation({
    mutationFn: async (params: { locationId: string; rawText: string }) => {
      const res = await api.post<ReceiptPreviewApiResponse>("/api/receipts/preview", {
        raw_text: params.rawText,
        location_id: params.locationId,
        source_filename: null,
      });
      return res.data;
    },
  });
}

export function useIngestReceiptText() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (params: { locationId: string; rawText: string }) => {
      const res = await api.post<ReceiptIngestApiResponse>("/api/receipts/ingest", {
        raw_text: params.rawText,
        location_id: params.locationId,
        source_filename: null,
      });
      return res.data;
    },
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ["inventory"] });
    },
  });
}

export function useCommitReceipt() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (params: {
      locationId: string;
      rawText: string;
      lines: ReceiptCommitLine[];
    }) => {
      const res = await api.post<ReceiptCommitApiResponse>("/api/receipts/commit", {
        raw_text: params.rawText,
        location_id: params.locationId,
        source_filename: null,
        lines: params.lines,
      });
      return res.data;
    },
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ["inventory"] });
    },
  });
}

export function useBarcodeLookup() {
  return useMutation({
    mutationFn: async (barcode: string) => {
      const res = await api.get<BarcodeLookupApiResponse>(`/api/barcodes/lookup/${encodeURIComponent(barcode.trim())}`);
      return res.data;
    },
  });
}
