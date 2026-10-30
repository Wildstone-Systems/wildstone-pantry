import { cleanup, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { renderWithProviders } from "@/test-utils";

const apiGet = vi.fn();
const apiPost = vi.fn();
const apiPut = vi.fn();
const apiDelete = vi.fn();
vi.mock("@/api/client", () => ({
  api: {
    get: (...a: unknown[]) => apiGet(...a),
    post: (...a: unknown[]) => apiPost(...a),
    put: (...a: unknown[]) => apiPut(...a),
    delete: (...a: unknown[]) => apiDelete(...a),
  },
}));

const RELAY = { ledger_seq: 0, pending: 0, delivered: 0, rejected: 0, last_delivered_at: null, last_error: null };
type Status = {
  connected: boolean;
  paired_at: string | null;
  keystone_url: string;
  publisher_token_set: boolean;
  web_url: string | null;
  web_url_problem: string | null;
  relay: typeof RELAY;
};
const DISCONNECTED: Status = {
  connected: false,
  paired_at: null,
  keystone_url: "",
  publisher_token_set: false,
  web_url: null,
  web_url_problem: null,
  relay: RELAY,
};
const CONNECTED: Status = {
  connected: true,
  paired_at: "2026-09-27T03:00:00+00:00",
  keystone_url: "https://keystone.example.internal",
  publisher_token_set: true,
  web_url: "https://pantry.example.internal",
  web_url_problem: null,
  relay: { ...RELAY, delivered: 12, pending: 1 },
};

let status: Status = DISCONNECTED;

async function openCard() {
  const { WildstoneCard } = await import("./WildstoneCard");
  renderWithProviders(<WildstoneCard />);
  const user = userEvent.setup();
  await user.click(screen.getByRole("button", { name: "Expand" }));
  return user;
}

describe("WildstoneCard", () => {
  beforeEach(() => {
    window.localStorage.clear();
    for (const f of [apiGet, apiPost, apiPut, apiDelete]) f.mockReset();
    status = DISCONNECTED;
    apiGet.mockImplementation((url: string) => {
      if (url === "/api/integrations/wildstone/connection") return Promise.resolve({ data: status });
      if (url === "/api/integrations/wildstone/members")
        return Promise.resolve({ data: [{ profile_id: "p1", display_name: "Sam", member: null }] });
      return Promise.resolve({ data: {} });
    });
  });
  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
  });

  it("shows a one-time code with this PantryOS's address, and notices when pairing completes", async () => {
    // Rendering and clicking may straddle a second on a loaded runner. Keep
    // countdown arithmetic fixed while real timers continue polling pairing.
    vi.spyOn(Date, "now").mockReturnValue(1_800_000_000_000);
    apiPost.mockResolvedValue({ data: { code: "KCTW-X54C", expires_in: 300 } });
    const user = await openCard();
    expect(await screen.findByText("Not connected")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Pair with Wildstone" }));
    expect(apiPost).toHaveBeenCalledWith("/api/integrations/wildstone/pairing");
    expect(await screen.findByTestId("pairing-code")).toHaveTextContent("KCTW-X54C");
    expect(screen.getByText(window.location.origin)).toBeInTheDocument();
    expect(screen.getByText(/Expires in 5:00|Expires in 4:59/)).toBeInTheDocument();

    // Wildstone completes the pairing; the card is polling and notices.
    status = CONNECTED;
    await waitFor(() => expect(screen.queryByTestId("pairing-code")).not.toBeInTheDocument(), { timeout: 5000 });
    expect(screen.getByText(/Paired with Wildstone at https:\/\/keystone.example.internal/)).toBeInTheDocument();
  }, 10000);

  it("when connected, shows delivery, tests, links members and asks before disconnecting", async () => {
    status = CONNECTED;
    apiPost.mockResolvedValue({ data: { ok: true, status: 201 } });
    apiPut.mockResolvedValue({ data: { profile_id: "p1", display_name: "Sam", member: "sam" } });
    apiDelete.mockResolvedValue({ data: DISCONNECTED });
    const user = await openCard();

    expect(await screen.findByText(/12 sent · 1 waiting · 0 refused/)).toBeInTheDocument();
    expect(screen.getByText("https://pantry.example.internal")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Send test event" }));
    expect(apiPost).toHaveBeenCalledWith("/api/integrations/wildstone/connection/test");
    expect(await screen.findByText("Wildstone received a test event.")).toBeInTheDocument();

    await user.type(await screen.findByLabelText("Wildstone member for Sam"), "sam");
    await user.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() =>
      expect(apiPut).toHaveBeenCalledWith("/api/integrations/wildstone/members/p1", { member: "sam" }),
    );

    await user.click(screen.getByRole("button", { name: "Disconnect" }));
    expect(apiDelete).not.toHaveBeenCalled();
    await user.click(screen.getByRole("button", { name: "Yes, disconnect" }));
    await waitFor(() => expect(apiDelete).toHaveBeenCalledWith("/api/integrations/wildstone/connection"));
  });

  it("says what web address Wildstone is told, or why none is sent", async () => {
    status = { ...DISCONNECTED, web_url_problem: "plain http is only for household-network addresses; use https" };
    await openCard();
    expect(await screen.findByText(/PANTRYOS_PUBLIC_URL is set but plain http/)).toBeInTheDocument();
    cleanup();
    window.localStorage.clear(); // the card remembers being expanded

    status = DISCONNECTED;
    await openCard();
    expect(await screen.findByText(/Set PANTRYOS_PUBLIC_URL/)).toBeInTheDocument();
  });
});
