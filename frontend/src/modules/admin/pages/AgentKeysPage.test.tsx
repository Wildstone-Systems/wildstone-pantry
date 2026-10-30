import { cleanup, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { renderWithProviders } from "@/test-utils";

// Keep the synthetic response token readable without a key-like source literal.
const mintedSecret = ["pk_", "fresh_", "secret_", "123"].join("");

const apiGet = vi.fn();
const apiPost = vi.fn();
const apiDelete = vi.fn();

vi.mock("@/api/client", () => ({
  api: {
    get: (...args: unknown[]) => apiGet(...args),
    post: (...args: unknown[]) => apiPost(...args),
    delete: (...args: unknown[]) => apiDelete(...args),
    interceptors: { response: { use: () => 0, eject: () => undefined } },
  },
  apiBaseUrl: () => "https://pantry.example.test",
}));

describe("Agent keys page — connect guide", () => {
  beforeEach(() => {
    apiGet.mockReset();
    apiPost.mockReset();
    apiDelete.mockReset();
    apiGet.mockResolvedValue({ data: { keys: [] } });
  });

  // RTL queries bind to document.body; without explicit cleanup the first
  // test's DOM leaks into the second (globals-based auto-cleanup is off here).
  afterEach(cleanup);

  it("shows the how-to-connect guide with copyable snippets", async () => {
    const user = userEvent.setup();
    const { AgentKeysPage } = await import("./AgentKeysPage");
    renderWithProviders(<AgentKeysPage />, { initialEntries: ["/admin/agent-keys"] });

    await waitFor(() => expect(screen.getByText("No agent keys yet.")).toBeInTheDocument());

    // Guide is collapsed by default; opens on click.
    await user.click(screen.getByRole("button", { name: "How to connect an agent" }));

    // The three snippets render with their copy buttons.
    expect(screen.getByText(/Briefing to paste to your agent/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Copy Quick test (terminal)" })).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: /Copy MCP client config/ }),
    ).toBeInTheDocument();

    // Without a freshly minted key, snippets carry the placeholder.
    expect(screen.getAllByText(/pk_your_key/).length).toBeGreaterThan(0);
    // The briefing points the agent at the API docs and the summary endpoint.
    expect(screen.getAllByText(/api\/agent\/summary/).length).toBeGreaterThan(0);
  });

  it("fills a freshly minted secret into the runnable snippets", async () => {
    const user = userEvent.setup();
    apiPost.mockResolvedValue({ data: { key: { id: "k1" }, secret: mintedSecret } });
    const { AgentKeysPage } = await import("./AgentKeysPage");
    // Scope queries to this render — the suite doesn't auto-cleanup between tests.
    const view = renderWithProviders(<AgentKeysPage />, { initialEntries: ["/admin/agent-keys"] });

    await user.type(view.getByPlaceholderText("e.g. Hermes agent"), "Hermes agent");
    await user.click(view.getByRole("button", { name: "Create" }));
    await waitFor(() =>
      expect(view.getByText(/won't be shown again/)).toBeInTheDocument(),
    );

    await user.click(view.getByRole("button", { name: "How to connect an agent" }));
    // Secret appears in the secret alert + curl + MCP config snippets.
    expect(view.getAllByText((text) => text.includes(mintedSecret)).length).toBeGreaterThanOrEqual(3);
    expect(view.queryAllByText(/pk_your_key/)).toHaveLength(0);
  });

  it("stops showing the secret when its key is revoked", async () => {
    const user = userEvent.setup();
    const mintedRow = {
      id: "k1",
      name: "Hermes agent",
      token_prefix: "pk_fr",
      scopes: "read,write",
      revoked: false,
      created_at: "2026-07-23T00:00:00Z",
      last_used_at: null,
    };
    let keys: (typeof mintedRow)[] = [];
    apiGet.mockImplementation(async () => ({ data: { keys } }));
    apiPost.mockImplementation(async () => {
      keys = [mintedRow];
      return { data: { key: { id: "k1" }, secret: mintedSecret } };
    });
    apiDelete.mockImplementation(async () => {
      keys = [{ ...mintedRow, revoked: true }];
      return { data: {} };
    });

    const { AgentKeysPage } = await import("./AgentKeysPage");
    const view = renderWithProviders(<AgentKeysPage />, { initialEntries: ["/admin/agent-keys"] });

    await user.type(view.getByPlaceholderText("e.g. Hermes agent"), "Hermes agent");
    await user.click(view.getByRole("button", { name: "Create" }));
    await waitFor(() => expect(view.getByText(/won't be shown again/)).toBeInTheDocument());

    await user.click(view.getByRole("button", { name: "Revoke" }));
    // The revoked credential disappears from the alert and the snippets
    // fall back to the placeholder.
    await waitFor(() => expect(view.queryByText(/won't be shown again/)).not.toBeInTheDocument());
    expect(view.queryAllByText((text) => text.includes(mintedSecret))).toHaveLength(0);
    await user.click(view.getByRole("button", { name: "How to connect an agent" }));
    expect(view.getAllByText(/pk_your_key/).length).toBeGreaterThan(0);
  });
});
