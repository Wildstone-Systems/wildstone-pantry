import { lazy } from "react";

import type { ModuleManifest } from "@/core/registry";
import { withSuspense } from "@/core/lazyRoute";
import { ROUTES } from "@/routes/paths";

const CatalogPage = lazy(() => import("./pages/CatalogPage").then((m) => ({ default: m.CatalogPage })));
const AddItemPage = lazy(() => import("./pages/AddItemPage").then((m) => ({ default: m.AddItemPage })));
const ScanPage = lazy(() => import("./pages/ScanPage").then((m) => ({ default: m.ScanPage })));
const ConversionReviewPage = lazy(() =>
  import("./pages/ConversionReviewPage").then((m) => ({ default: m.ConversionReviewPage })),
);
const DataQualityPage = lazy(() =>
  import("./pages/DataQualityPage").then((m) => ({ default: m.DataQualityPage })),
);

export const catalogManifest: ModuleManifest = {
  id: "catalog",
  routes: [
    { path: ROUTES.catalog, element: withSuspense(<CatalogPage />) },
    { path: ROUTES.catalogAddItem, element: withSuspense(<AddItemPage />) },
    { path: ROUTES.scan, element: withSuspense(<ScanPage />) },
    { path: ROUTES.reviewConversions, element: withSuspense(<ConversionReviewPage />) },
    { path: ROUTES.reviewDataQuality, element: withSuspense(<DataQualityPage />) },
  ],
};
