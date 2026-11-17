import { lazy } from "react";

import type { ModuleManifest } from "@/core/registry";
import { withSuspense } from "@/core/lazyRoute";
import { ROUTES } from "@/routes/paths";

const SettingsPage = lazy(() => import("./pages/SettingsPage").then((m) => ({ default: m.SettingsPage })));
const PairDevicePage = lazy(() =>
  import("./pages/PairDevicePage").then((m) => ({ default: m.PairDevicePage })),
);
const SetupPage = lazy(() => import("./pages/SetupPage").then((m) => ({ default: m.SetupPage })));

export const accountManifest: ModuleManifest = {
  id: "account",
  publicRoutes: [
    { path: ROUTES.setup, element: withSuspense(<SetupPage />) },
    { path: ROUTES.pair, element: withSuspense(<PairDevicePage />) },
  ],
  routes: [{ path: ROUTES.settings, element: withSuspense(<SettingsPage />) }],
};
