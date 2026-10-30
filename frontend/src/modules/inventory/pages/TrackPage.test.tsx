import { cleanup, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { renderWithProviders } from "@/test-utils";

/**
 * Tracking worklist behaviour (ADR 0023 D4/D6/D7).
 *
 * The assertions that matter here are about *safety of a one-tap surface*: the
 * right endpoint for each verb (waste must not be filed as usage), undo offered
 * for anything that wrote to the ledger, and a confirmation on the single action
 * that silences future signal.
 */

const OPEN_TUB = {
  source: "open",
  item_id: "item-yogurt",
  item_name: "Yogurt",
  location_id: "loc-1",
  lot_id: "lot-open",
  quantity_remaining: "0.5",
  unit: "qt",
  expiration_date: null,
  days_until_expiration: null,
  status: "open",
  sealed_count: null,
  container_size: "1",
};

const EXPIRING_CREAM = {
  ...OPEN_TUB,
  source: "expiring",
  item_id: "item-cream",
  item_name: "Cream",
  lot_id: "lot-cream",
  quantity_remaining: "1",
  expiration_date: "2026-08-10",
  days_until_expiration: 2,
  status: "sealed",
};

// Rows a test can swap before rendering; restored in beforeEach so one test's
// override never leaks into the next.
let worklistRows: unknown[] = [OPEN_TUB, EXPIRING_CREAM];

const defaultGet = async (url: string) => {
  if (url.startsWith("/api/locations")) {
    return { data: { locations: [{ id: "loc-1", name: "Fridge", description: null, is_active: true }] } };
  }
  if (url.includes("/api/inventory/worklist")) {
    return { data: { location_id: "loc-1", rows: worklistRows } };
  }
  if (url.startsWith("/api/household/profiles")) return { data: { profiles: [] } };
  return { data: {} };
};

const apiGet = vi.fn(defaultGet);

const apiPost = vi.fn(async (url: string, _body?: unknown) => {
  if (url === "/api/inventory/remove") return { data: { transaction_ids: ["tx-remove"] } };
  return { data: { transaction_id: "tx-1", already_resolved: false } };
});
const apiPut = vi.fn(async (_url: string, _body?: unknown) => ({ data: {} }));

vi.mock("@/api/client", () => ({
  api: {
    get: (url: string) => apiGet(url),
    post: (url: string, body?: unknown) => apiPost(url, body),
    put: (url: string, body?: unknown) => apiPut(url, body),
  },
}));

async function renderPage() {
  const { TrackPage } = await import("./TrackPage");
  renderWithProviders(<TrackPage />);
  await screen.findByText("Yogurt");
}

describe("TrackPage (unified tracking worklist)", () => {
  beforeEach(() => {
    window.localStorage.clear();
    apiPost.mockClear();
    apiPut.mockClear();
    worklistRows = [OPEN_TUB, EXPIRING_CREAM];
    apiGet.mockImplementation(defaultGet);
  });
  // Auto-cleanup isn't on (vitest runs without RTL globals), so unmount
  // explicitly or the next render finds two of every row.
  afterEach(() => {
    cleanup();
    window.localStorage.clear();
  });

  it("finishing a container targets that lot, not a quantity guess", async () => {
    const user = userEvent.setup();
    await renderPage();

    await user.click(screen.getAllByRole("button", { name: "Finished it" })[0]);

    await waitFor(() => expect(apiPost).toHaveBeenCalledWith("/api/inventory/lots/lot-open/finish", undefined));
  });

  it("throwing something out records waste, never consumption", async () => {
    const user = userEvent.setup();
    await renderPage();

    await user.click(screen.getAllByRole("button", { name: "Threw it out" })[0]);

    await waitFor(() => expect(apiPost).toHaveBeenCalledWith("/api/inventory/lots/lot-open/spoil", undefined));
    // The consume endpoints must not be involved — waste filed as usage would
    // corrupt the very distinction this surface exists to capture.
    const urls = apiPost.mock.calls.map((call) => call[0]);
    expect(urls.some((url) => String(url).endsWith("/finish"))).toBe(false);
    expect(urls).not.toContain("/api/inventory/remove");
  });

  it("offers undo for an action that wrote to the ledger", async () => {
    const user = userEvent.setup();
    await renderPage();

    await user.click(screen.getAllByRole("button", { name: "Finished it" })[0]);

    const undo = await screen.findByRole("button", { name: "Undo" });
    await user.click(undo);

    await waitFor(() =>
      expect(apiPost).toHaveBeenCalledWith("/api/inventory/transactions/tx-1/reverse", undefined),
    );
  });

  it("'still good' relabels the lot instead of pretending it was used", async () => {
    const user = userEvent.setup();
    await renderPage();

    await user.click(await screen.findByRole("button", { name: "Still good" }));

    await waitFor(() => {
      const call = apiPost.mock.calls.find((c) => String(c[0]).endsWith("/relabel"));
      expect(call?.[0]).toBe("/api/inventory/lots/lot-cream/relabel");
      expect(call?.[1]).toHaveProperty("expiration_date");
    });
  });

  it("ignoring an item asks first, and does nothing if cancelled", async () => {
    const user = userEvent.setup();
    await renderPage();

    await user.click(await screen.findByRole("button", { name: "Ignore" }));
    await screen.findByText(/Stop tracking this item's best-by\?/);
    expect(apiPut).not.toHaveBeenCalled();

    await user.click(screen.getByRole("button", { name: "Cancel" }));
    expect(apiPut).not.toHaveBeenCalled();
  });

  it("confirming the ignore flags the item", async () => {
    const user = userEvent.setup();
    await renderPage();

    await user.click(await screen.findByRole("button", { name: "Ignore" }));
    await user.click(await screen.findByRole("button", { name: "Stop tracking it" }));

    await waitFor(() =>
      expect(apiPut).toHaveBeenCalledWith("/api/items/item-cream", { ignore_expiry: true }),
    );
  });

  it("only expiring rows offer the expiry-specific verbs", async () => {
    await renderPage();
    // Two rows, but "Still good" / "Ignore" belong to the dated one alone.
    expect(screen.getAllByRole("button", { name: "Finished it" })).toHaveLength(2);
    expect(screen.getAllByRole("button", { name: "Still good" })).toHaveLength(1);
  });

  it("offers undo on a lot-less 'recent' row too", async () => {
    // This path posts to /remove rather than a lot verb, but it is just as
    // mistakeable — an accidental tap has to be correctable from here.
    const user = userEvent.setup();
    worklistRows = [{ ...OPEN_TUB, source: "recent", lot_id: null, item_name: "Coffee", unit: "each" }];

    const { TrackPage } = await import("./TrackPage");
    renderWithProviders(<TrackPage />);
    await screen.findByText("Coffee");

    await user.click(screen.getByRole("button", { name: "Used one" }));

    await waitFor(() => expect(apiPost).toHaveBeenCalledWith("/api/inventory/remove", expect.anything()));
    expect(await screen.findByRole("button", { name: "Undo" })).toBeInTheDocument();
  });

  it("warns when no profile is selected, so taps aren't misattributed", async () => {
    await renderPage();
    expect(await screen.findByText(/pick a profile above/i)).toBeInTheDocument();
  });
});
