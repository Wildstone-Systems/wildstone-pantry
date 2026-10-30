import { cleanup, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { renderWithProviders } from "@/test-utils";

const apiGet = vi.fn();
const apiPut = vi.fn();

vi.mock("@/api/client", () => ({
  api: {
    get: (...a: unknown[]) => apiGet(...a),
    put: (...a: unknown[]) => apiPut(...a),
    post: vi.fn(),
    delete: vi.fn(),
  },
}));

const CONFIG_VIEW = {
  key: "oidc",
  values: {
    issuer: "https://auth.example.lan/application/o/pantryos/",
    client_id: "pantryos",
    scopes: "openid profile email groups",
    groups_claim: "groups",
    users_group: "pantryos_users",
    admin_group: "pantryos_admin",
    button_label: "Sign in with SSO",
  },
  secret_set: { client_secret: true },
  has_override: true,
  from_env_only: false,
  secrets_available: true,
};

const STATUS = { enabled: true, button_label: "Sign in with SSO", local_login_hidden: false };

describe("OidcCard", () => {
  beforeEach(() => {
    // The card's expand/collapse state persists in localStorage across
    // renders (by design, for real usage) — clear it so each test starts
    // from the same default-collapsed state regardless of run order.
    window.localStorage.clear();
    apiGet.mockReset();
    apiPut.mockReset();
    apiGet.mockImplementation((url: string) => {
      if (url === "/api/admin/integrations/oidc") return Promise.resolve({ data: CONFIG_VIEW });
      if (url === "/api/auth/oidc/status") return Promise.resolve({ data: STATUS });
      return Promise.resolve({ data: {} });
    });
  });

  afterEach(cleanup);

  it("renders values from GET and shows the redirect URI", async () => {
    const { OidcCard } = await import("./OidcCard");
    renderWithProviders(<OidcCard />);

    await waitFor(() => expect(screen.getByDisplayValue("pantryos")).toBeInTheDocument());
    expect(screen.getByDisplayValue("https://auth.example.lan/application/o/pantryos/")).toBeInTheDocument();
    expect(screen.getByText(`${window.location.origin}/api/auth/oidc/callback`)).toBeInTheDocument();
    // Client secret is set server-side — placeholder says so, field stays blank.
    expect(screen.getByPlaceholderText("unchanged")).toBeInTheDocument();
  });

  it("submits changed values via PUT", async () => {
    const user = userEvent.setup();
    apiPut.mockResolvedValue({ data: CONFIG_VIEW });
    const { OidcCard } = await import("./OidcCard");
    renderWithProviders(<OidcCard />);

    await waitFor(() => expect(screen.getByDisplayValue("pantryos")).toBeInTheDocument());
    // The card starts collapsed; expand it to reach the Save button.
    await user.click(screen.getByRole("button", { name: "Expand" }));

    const clientIdInput = screen.getByDisplayValue("pantryos");
    await user.clear(clientIdInput);
    await user.type(clientIdInput, "pantryos-new");

    await user.click(screen.getByRole("button", { name: "Save" }));

    await waitFor(() =>
      expect(apiPut).toHaveBeenCalledWith(
        "/api/admin/integrations/oidc",
        expect.objectContaining({ updates: expect.objectContaining({ client_id: "pantryos-new" }) }),
      ),
    );
  });

  it("wires the hide-local-login switch to the visibility endpoint once configured", async () => {
    const user = userEvent.setup();
    apiPut.mockImplementation((url: string) => {
      if (url === "/api/auth/local-login-visibility") return Promise.resolve({ data: { hidden: true } });
      return Promise.resolve({ data: CONFIG_VIEW });
    });
    const { OidcCard } = await import("./OidcCard");
    renderWithProviders(<OidcCard />);

    // The card starts collapsed; expand it to reach the switch.
    await user.click(await screen.findByRole("button", { name: "Expand" }));

    const toggle = await screen.findByRole("switch", { name: /Hide local password login/ });
    await waitFor(() => expect(toggle).not.toBeDisabled());
    await user.click(toggle);

    await waitFor(() =>
      expect(apiPut).toHaveBeenCalledWith("/api/auth/local-login-visibility", { hidden: true }),
    );
  });
});
