import { createContext, type ReactNode, useCallback, useContext, useEffect, useMemo, useState } from "react";

import { api } from "@/api/client";
import type { Account, AuthTokenResponse } from "@/api/types";
import { clearToken, getToken, setToken } from "@/auth";

/**
 * Admin session state (ADR 0004). The account gates the protected admin surface
 * only — everyday kiosk use never needs this. `account` is null when logged out.
 */
type AuthContextValue = {
  account: Account | null;
  isLoading: boolean;
  isAdmin: boolean;
  login: (email: string, password: string) => Promise<void>;
  bootstrap: (email: string, password: string) => Promise<void>;
  logout: () => void;
  /**
   * Adopt a token minted outside the password-login flow (SSO callback
   * fragment, ADR 0020) and hydrate the account exactly like `login()` does.
   */
  refreshSession: () => Promise<void>;
};

const AuthContext = createContext<AuthContextValue | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [account, setAccount] = useState<Account | null>(null);
  const [isLoading, setIsLoading] = useState<boolean>(() => Boolean(getToken()));

  const refresh = useCallback(async () => {
    if (!getToken()) {
      setAccount(null);
      setIsLoading(false);
      return;
    }
    try {
      const res = await api.get<Account>("/api/auth/me");
      setAccount(res.data);
    } catch {
      // Invalid/expired token — drop it and fall back to logged-out.
      clearToken();
      setAccount(null);
    } finally {
      setIsLoading(false);
    }
  }, []);

  // Hydrate on mount if a token is stored.
  useEffect(() => {
    void refresh();
  }, [refresh]);

  // Session-expiry handling: a 401 on a request that carried a bearer token
  // means the admin session lapsed. Kiosk 401s (no token) are left untouched.
  useEffect(() => {
    const response = api.interceptors?.response;
    if (!response) return; // tolerate a partial api mock in unit tests
    const id = response.use(
      (r) => r,
      (error) => {
        const status = error?.response?.status;
        const hadAuth = Boolean(error?.config?.headers?.Authorization);
        if (status === 401 && hadAuth) {
          clearToken();
          setAccount(null);
        }
        return Promise.reject(error);
      },
    );
    return () => response.eject(id);
  }, []);

  const login = useCallback(async (email: string, password: string) => {
    const res = await api.post<AuthTokenResponse>("/api/auth/login", { email, password });
    setToken(res.data.token);
    const me = await api.get<Account>("/api/auth/me");
    setAccount(me.data);
  }, []);

  const bootstrap = useCallback(
    async (email: string, password: string) => {
      // Create the first admin (kiosk deployments start with no accounts), then log in.
      await api.post("/api/auth/bootstrap-admin", { email, password });
      await login(email, password);
    },
    [login],
  );

  const logout = useCallback(() => {
    clearToken();
    setAccount(null);
  }, []);

  const value = useMemo<AuthContextValue>(
    () => ({
      account,
      isLoading,
      isAdmin: account?.role === "admin",
      login,
      bootstrap,
      logout,
      refreshSession: refresh,
    }),
    [account, isLoading, login, bootstrap, logout, refresh],
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthContextValue {
  const ctx = useContext(AuthContext);
  if (!ctx) {
    throw new Error("useAuth must be used within AuthProvider");
  }
  return ctx;
}
