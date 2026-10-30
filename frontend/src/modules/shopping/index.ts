/** Shopping domain module.
 *
 * Self-contained module for shopping operations.
 */

// Hooks
export {
  usePurchases,
  usePurchaseDetail,
  usePreviewPurchaseCsv,
  useImportPurchaseCsv,
  useIngestReceiptImage,
  usePreviewReceipt,
  useIngestReceiptText,
  useCommitReceipt,
  useBarcodeLookup,
} from "./hooks/shopping-hooks";

export {
  useShoppingList,
  useAddShoppingItem,
  useUpdateShoppingItem,
  useDeleteShoppingItem,
} from "./hooks/shopping-list-hooks";

export type {
  PurchaseRead,
  PurchaseLine,
  PurchaseDisplayedLine,
  PurchaseDetailResponse,
  PurchasePreviewApiResponse,
  PurchaseImportApiResponse,
  ReceiptLine,
  ReceiptIngestApiResponse,
  ReceiptPreviewLine,
  ReceiptPreviewApiResponse,
  ReceiptCommitApiResponse,
  BarcodeLookupApiResponse,
} from "./hooks/shopping-hooks";
