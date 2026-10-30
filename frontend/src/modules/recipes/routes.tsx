import { lazy } from "react";

import type { ModuleManifest } from "@/core/registry";
import { withSuspense } from "@/core/lazyRoute";
import { ROUTES } from "@/routes/paths";

const RecipesPage = lazy(() => import("./pages/RecipesPage").then((m) => ({ default: m.RecipesPage })));
const EditRecipePage = lazy(() => import("./pages/EditRecipePage").then((m) => ({ default: m.EditRecipePage })));
const RecipeViewPage = lazy(() => import("./pages/RecipeViewPage").then((m) => ({ default: m.RecipeViewPage })));
const CookAlongPage = lazy(() => import("./pages/CookAlongPage").then((m) => ({ default: m.CookAlongPage })));
const AdHocCookPage = lazy(() => import("./pages/AdHocCookPage").then((m) => ({ default: m.AdHocCookPage })));

export const recipesManifest: ModuleManifest = {
  id: "recipes",
  routes: [
    { path: ROUTES.recipes, element: withSuspense(<RecipesPage />) },
    { path: ROUTES.recipesNew, element: withSuspense(<EditRecipePage />) },
    { path: ROUTES.recipesEdit, element: withSuspense(<EditRecipePage />) },
    { path: ROUTES.recipesView, element: withSuspense(<RecipeViewPage />) },
    { path: ROUTES.recipesCook, element: withSuspense(<CookAlongPage />) },
    { path: ROUTES.cook, element: withSuspense(<AdHocCookPage />) },
    { path: ROUTES.cookSession, element: withSuspense(<AdHocCookPage />) },
  ],
};
