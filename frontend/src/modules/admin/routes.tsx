import { lazy } from "react";

import { withSuspense } from "@/core/lazyRoute";
import type { ModuleManifest } from "@/core/registry";
import { ROUTES } from "@/routes/paths";

import { AdminGuard } from "./components/AdminGuard";

const AdminHome = lazy(() => import("./pages/AdminHome").then((m) => ({ default: m.AdminHome })));
const HouseholdPage = lazy(() => import("./pages/HouseholdPage").then((m) => ({ default: m.HouseholdPage })));
const IntegrationsPage = lazy(() => import("./pages/IntegrationsPage").then((m) => ({ default: m.IntegrationsPage })));
const AgentKeysPage = lazy(() => import("./pages/AgentKeysPage").then((m) => ({ default: m.AgentKeysPage })));

// The whole /admin subtree sits behind AdminGuard: a real admin login is
// required even in kiosk mode (ADR 0004). Rendered inside the AppLayout shell.
export const adminManifest: ModuleManifest = {
  id: "admin",
  routes: [
    {
      path: ROUTES.admin,
      element: <AdminGuard />,
      children: [
        { index: true, element: withSuspense(<AdminHome />) },
        { path: "household", element: withSuspense(<HouseholdPage />) },
        { path: "integrations", element: withSuspense(<IntegrationsPage />) },
        { path: "agent-keys", element: withSuspense(<AgentKeysPage />) },
      ],
    },
  ],
};
