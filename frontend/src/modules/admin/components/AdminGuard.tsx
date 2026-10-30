import { Outlet } from "react-router-dom";

import { LoadingPage } from "@/components/LoadingPage";
import { useAuth } from "@/state/AuthProvider";

import { LoginPage } from "../pages/LoginPage";

/**
 * Gate for the protected admin surface (ADR 0004). A real admin login is
 * required here even in kiosk mode — the open kiosk identity is never a path in.
 */
export function AdminGuard() {
  const { isLoading, isAdmin } = useAuth();

  if (isLoading) {
    return <LoadingPage title="PantryOS" message="Checking admin session..." />;
  }
  if (!isAdmin) {
    return <LoginPage />;
  }
  return <Outlet />;
}
