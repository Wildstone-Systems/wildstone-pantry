/**
 * Central module list. Importing the manifests here is the single place that
 * knows the set of feature modules (mirrors backend modules/__init__.py).
 */
import { accountManifest } from "@/modules/account/routes";
import { adminManifest } from "@/modules/admin/routes";
import { catalogManifest } from "@/modules/catalog/routes";
import { feedbackManifest } from "@/modules/feedback/routes";
import { insightsManifest } from "@/modules/insights/routes";
import { inventoryManifest } from "@/modules/inventory/routes";
import { mealsManifest } from "@/modules/meals/routes";
import { recipesManifest } from "@/modules/recipes/routes";
import { shoppingManifest } from "@/modules/shopping/routes";

import type { ModuleManifest } from "./registry";

export const allManifests: ModuleManifest[] = [
  catalogManifest,
  inventoryManifest,
  recipesManifest,
  mealsManifest,
  shoppingManifest,
  insightsManifest,
  feedbackManifest,
  accountManifest,
  adminManifest,
];
