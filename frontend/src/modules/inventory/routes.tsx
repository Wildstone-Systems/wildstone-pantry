import { lazy } from "react";

import type { ModuleManifest } from "@/core/registry";
import { withSuspense } from "@/core/lazyRoute";
import { ROUTES } from "@/routes/paths";

const DashboardPage = lazy(() => import("./pages/DashboardPage").then((m) => ({ default: m.DashboardPage })));
const InventoryPage = lazy(() => import("./pages/InventoryPage").then((m) => ({ default: m.InventoryPage })));
const AddLocationPage = lazy(() => import("./pages/AddLocationPage").then((m) => ({ default: m.AddLocationPage })));
const ScanStationPage = lazy(() => import("./pages/ScanStationPage").then((m) => ({ default: m.ScanStationPage })));
const TrackPage = lazy(() => import("./pages/TrackPage").then((m) => ({ default: m.TrackPage })));

export const inventoryManifest: ModuleManifest = {
  id: "inventory",
  routes: [
    { index: true, element: withSuspense(<DashboardPage />) },
    { path: ROUTES.inventory, element: withSuspense(<InventoryPage />) },
    { path: ROUTES.track, element: withSuspense(<TrackPage />) },
    { path: ROUTES.scanStation, element: withSuspense(<ScanStationPage />) },
    { path: ROUTES.catalogAddLocation, element: withSuspense(<AddLocationPage />) },
  ],
};
