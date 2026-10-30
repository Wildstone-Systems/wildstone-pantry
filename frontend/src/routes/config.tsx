import type { RouteObject } from "react-router-dom";

import { buildRoutes } from "@/core/app";

// Routes are assembled from the registered feature-module manifests
// (see src/core/app.tsx). Add a module → register its manifest in
// src/core/modules.ts; no edits needed here.
export const routes: RouteObject[] = buildRoutes();
