import { lazy } from "react";

import type { ModuleManifest } from "@/core/registry";
import { withSuspense } from "@/core/lazyRoute";
import { ROUTES } from "@/routes/paths";

const InsightsPage = lazy(() => import("./pages/InsightsPage").then((m) => ({ default: m.InsightsPage })));

export const insightsManifest: ModuleManifest = {
  id: "insights",
  routes: [{ path: ROUTES.insights, element: withSuspense(<InsightsPage />) }],
};
