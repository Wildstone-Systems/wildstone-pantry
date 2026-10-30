import { cleanup, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { renderWithProviders } from "@/test-utils";

const apiGet = vi.fn();
const apiPost = vi.fn();
const apiPut = vi.fn();

vi.mock("@/api/client", () => ({
  api: {
    get: (...a: unknown[]) => apiGet(...a),
    post: (...a: unknown[]) => apiPost(...a),
    put: (...a: unknown[]) => apiPut(...a),
    delete: vi.fn(),
  },
}));

const login = vi.fn();
const bootstrap = vi.fn();
const refreshSession = vi.fn();

vi.mock("@/state/AuthProvider", () => ({
  useAuth: () => ({
    account: null,
    isLoading: false,
    isAdmin: false,
    login,
    bootstrap,
    logout: vi.fn(),
    refreshSession,
  }),
}));

const DISABLED_STATUS = { enabled: false, button_label: "Sign in with SSO", local_login_hidden: false };

describe("LoginPage — SSO (ADR 0020)", () => {
  beforeEach(() => {
    apiGet.mockReset();
    apiPost.mockReset();
    apiPut.mockReset();
    login.mockReset();
    bootstrap.mockReset();
    refreshSession.mockReset();
    window.history.replaceState(null, "", "/login");
    apiGet.mockImplementation((url: string) => {
      if (url === "/api/auth/setup-status") return Promise.resolve({ data: { needs_setup: false } });
      if (url === "/api/auth/oidc/status") return Promise.resolve({ data: DISABLED_STATUS });
      return Promise.resolve({ data: {} });
    });
  });

  afterEach(() => {
    cleanup();
    window.localStorage.clear();
    window.history.replaceState(null, "", "/login");
  });

  async function renderPage() {
    const { LoginPage } = await import("./LoginPage");
    renderWithProviders(<LoginPage />, { initialEntries: ["/login"] });
  }

  it("shows the SSO button with the configured label when enabled, and navigates on click", async () => {
    const user = userEvent.setup();
    apiGet.mockImplementation((url: string) => {
      if (url === "/api/auth/setup-status") return Promise.resolve({ data: { needs_setup: false } });
      if (url === "/api/auth/oidc/status")
        return Promise.resolve({ data: { enabled: true, button_label: "Sign in with Household SSO", local_login_hidden: false } });
      return Promise.resolve({ data: {} });
    });
    const assign = vi.fn();
    const originalLocation = window.location;
    // jsdom's `location.assign` throws "not implemented (navigation)" and the
    // property isn't configurable for spyOn — swap the whole object instead.
    Object.defineProperty(window, "location", {
      configurable: true,
      value: { ...originalLocation, assign },
    });

    await renderPage();

    const ssoButton = await screen.findByRole("button", { name: "Sign in with Household SSO" });
    await user.click(ssoButton);

    expect(assign).toHaveBeenCalledWith("/api/auth/oidc/login");

    // The password form is still present (break-glass, never removed).
    expect(screen.getByLabelText("Email")).toBeInTheDocument();

    Object.defineProperty(window, "location", { configurable: true, value: originalLocation });
  });

  it("does not show an SSO button when status says disabled", async () => {
    await renderPage();
    await waitFor(() => expect(apiGet).toHaveBeenCalledWith("/api/auth/oidc/status"));
    expect(screen.queryByRole("button", { name: "Sign in with SSO" })).not.toBeInTheDocument();
  });

  it("adopts an #sso_token fragment: stores the token and clears the hash", async () => {
    window.history.replaceState(null, "", "/login#sso_token=abc123");

    await renderPage();

    await waitFor(() => expect(window.localStorage.getItem("auth_token")).toBe("abc123"));
    expect(window.location.hash).toBe("");
    expect(refreshSession).toHaveBeenCalled();
  });

  it("shows an #sso_error fragment in the alert slot and clears the hash", async () => {
    window.history.replaceState(null, "", "/login#sso_error=Access%20denied");

    await renderPage();

    await waitFor(() => expect(screen.getByText("Access denied")).toBeInTheDocument());
    expect(window.location.hash).toBe("");
  });

  it("collapses the password form when local_login_hidden is true, and the reveal link brings it back", async () => {
    const user = userEvent.setup();
    apiGet.mockImplementation((url: string) => {
      if (url === "/api/auth/setup-status") return Promise.resolve({ data: { needs_setup: false } });
      if (url === "/api/auth/oidc/status")
        return Promise.resolve({ data: { enabled: true, button_label: "Sign in with SSO", local_login_hidden: true } });
      return Promise.resolve({ data: {} });
    });

    await renderPage();

    await screen.findByRole("button", { name: "Sign in with SSO" });
    expect(screen.queryByLabelText("Email")).not.toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Use local admin login" }));
    expect(screen.getByLabelText("Email")).toBeInTheDocument();
  });
});
