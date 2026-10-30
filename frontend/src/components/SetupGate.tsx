import { Navigate, Outlet, useLocation } from "react-router-dom";

import { useSetupStatus } from "../api/hooks";
import { ROUTES } from "../routes/paths";

/**
 * Route a fresh instance to the first-run wizard (ADR 0018 §7).
 *
 * Deliberately non-blocking, unlike the removed LicenseGuard: while status is
 * loading — or if it errors — the app renders normally. Only a definitive
 * "setup not completed" redirects. A cold backend can never strand the UI on
 * a setup screen.
 */
export function SetupGate() {
  const { data } = useSetupStatus();
  const location = useLocation();

  if (data && !data.completed && location.pathname !== ROUTES.setup) {
    return <Navigate to={ROUTES.setup} replace />;
  }
  return <Outlet />;
}
