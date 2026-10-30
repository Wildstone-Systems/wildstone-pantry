import { Navigate, type RouteObject } from "react-router-dom";

import { AppLayout } from "@/components/AppLayout";
import { SetupGate } from "@/components/SetupGate";
import { ROUTES } from "@/routes/paths";

import { allManifests } from "./modules";
import { moduleAppRoutes, modulePublicRoutes, registerModule } from "./registry";

// Register every feature module once, at import time.
allManifests.forEach(registerModule);

/** Assemble the application route tree from registered module manifests. */
export function buildRoutes(): RouteObject[] {
  return [
    ...modulePublicRoutes(),
    {
      // SetupGate only redirects a definitively-fresh instance to /setup; it
      // never blocks on loading or errors (ADR 0018 §7). Beyond that there is
      // no app-wide gate: local features are never license-gated (ADR 0018),
      // and the kiosk is open (everyday use needs no login). Only the /admin
      // subtree is gated, by the admin module's own AdminGuard + AuthProvider
      // (ADR 0004).
      element: <SetupGate />,
      children: [
        {
          path: ROUTES.dashboard,
          element: <AppLayout />,
          children: [
            ...moduleAppRoutes(),
            { path: "*", element: <Navigate to={ROUTES.dashboard} replace /> },
          ],
        },
      ],
    },
  ];
}
