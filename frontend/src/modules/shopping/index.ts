/** Shopping domain module.
 *
 * Self-contained module for shopping operations.
 */

// Hooks
export { usePurchases, usePurchaseDetail, useBarcodeLookup } from "./hooks/shopping-hooks";

export {
  useShoppingList,
  useAddShoppingItem,
  useUpdateShoppingItem,
  useDeleteShoppingItem,
} from "./hooks/shopping-list-hooks";

export type {
  PurchaseRead,
  PurchaseLine,
  PurchaseDetailResponse,
  BarcodeLookupApiResponse,
} from "./hooks/shopping-hooks";
