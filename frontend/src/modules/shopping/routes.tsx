import { lazy } from "react";

import type { ModuleManifest } from "@/core/registry";
import { withSuspense } from "@/core/lazyRoute";
import { ROUTES } from "@/routes/paths";

const IngestPage = lazy(() => import("./pages/IngestPage").then((m) => ({ default: m.IngestPage })));
const PurchasesPage = lazy(() => import("./pages/PurchasesPage").then((m) => ({ default: m.PurchasesPage })));
const EmailReceiptsPage = lazy(() => import("./pages/EmailReceiptsPage").then((m) => ({ default: m.EmailReceiptsPage })));
const ShoppingListPage = lazy(() => import("./pages/ShoppingListPage").then((m) => ({ default: m.ShoppingListPage })));
const HandoffSessionPage = lazy(() =>
  import("./pages/HandoffSessionPage").then((m) => ({ default: m.HandoffSessionPage })),
);

export const shoppingManifest: ModuleManifest = {
  id: "shopping",
  routes: [
    { path: ROUTES.shoppingList, element: withSuspense(<ShoppingListPage />) },
    { path: ROUTES.shoppingHandoff, element: withSuspense(<HandoffSessionPage />) },
    { path: ROUTES.ingest, element: withSuspense(<IngestPage />) },
    { path: ROUTES.purchases, element: withSuspense(<PurchasesPage />) },
    { path: ROUTES.emailReceipts, element: withSuspense(<EmailReceiptsPage />) },
  ],
};
