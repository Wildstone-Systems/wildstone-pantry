import { lazy } from "react";

import { withSuspense } from "@/core/lazyRoute";
import type { ModuleManifest } from "@/core/registry";
import { ROUTES } from "@/routes/paths";

const MealPlanPage = lazy(() => import("./pages/MealPlanPage").then((m) => ({ default: m.MealPlanPage })));

export const mealPlanManifest: ModuleManifest = {
  id: "mealplan",
  routes: [{ path: ROUTES.mealPlan, element: withSuspense(<MealPlanPage />) }],
};
