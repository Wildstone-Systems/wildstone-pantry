import { lazy } from "react";

import { withSuspense } from "@/core/lazyRoute";
import type { ModuleManifest } from "@/core/registry";
import { ROUTES } from "@/routes/paths";

const LogMealPage = lazy(() => import("./pages/LogMealPage").then((m) => ({ default: m.LogMealPage })));
const CaloriesPage = lazy(() => import("./pages/CaloriesPage").then((m) => ({ default: m.CaloriesPage })));

export const mealsManifest: ModuleManifest = {
  id: "meals",
  routes: [
    { path: ROUTES.logMeal, element: withSuspense(<LogMealPage />) },
    { path: ROUTES.calories, element: withSuspense(<CaloriesPage />) },
  ],
};
