import { lazy } from "react";

import type { ModuleManifest } from "@/core/registry";
import { withSuspense } from "@/core/lazyRoute";
import { ROUTES } from "@/routes/paths";

const SettingsPage = lazy(() => import("./pages/SettingsPage").then((m) => ({ default: m.SettingsPage })));
const SetupPage = lazy(() => import("./pages/SetupPage").then((m) => ({ default: m.SetupPage })));

export const accountManifest: ModuleManifest = {
  id: "account",
  publicRoutes: [{ path: ROUTES.setup, element: withSuspense(<SetupPage />) }],
  routes: [{ path: ROUTES.settings, element: withSuspense(<SettingsPage />) }],
};
