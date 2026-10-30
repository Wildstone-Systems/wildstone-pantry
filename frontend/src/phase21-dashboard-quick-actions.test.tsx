import { cleanup, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { renderWithProviders } from "./test-utils";

const postMock = vi.fn().mockResolvedValue({ data: {} });

vi.mock("./api/client", () => ({
  api: {
    post: (...args: unknown[]) => postMock(...args),
    get: vi.fn().mockResolvedValue({ data: {} }),
  },
}));

const emptyScoped = { data: [], isLoading: false, isFetching: false, refetch: () => {} };

vi.mock("./modules/inventory/hooks/scoped-hooks", () => ({
  useScopedExpiring: () => emptyScoped,
  useScopedRestock: () => ({
    ...emptyScoped,
    // Intentionally out of order: priority 3 (out of stock) is most urgent and
    // must render before priority 1.
    data: [
      { item_id: "lo", item_name: "Low urgency item", current_quantity: 2, priority: 1, reason: "low", recommendation: "buy", location_id: "loc-1", location_name: "Main Pantry" },
      { item_id: "hi", item_name: "Urgent item", current_quantity: 0, priority: 3, reason: "out", recommendation: "buy", location_id: "loc-1", location_name: "Main Pantry" },
    ],
  }),
  useScopedRecipeSuggestions: () => emptyScoped,
  useScopedInventory: () => ({
    ...emptyScoped,
    data: [
      {
        inventory_id: "inv-1",
        item_id: "item-1",
        item_name: "Milk",
        location_id: "loc-1",
        location_name: "Main Pantry",
        quantity: 2,
        unit: "each",
        expiration_date: null,
        updated_at: "2026-02-14T00:00:00.000Z",
      },
    ],
  }),
  useScopedTrends: () => ({
    ...emptyScoped,
    data: [
      {
        item_id: "item-1",
        item_name: "Milk",
        avg_daily_usage: 2,
        days_to_depletion: null,
        sampled_days: 5,
        location_id: "loc-1",
        location_name: "Main Pantry",
      },
    ],
  }),
}));

vi.mock("./modules/catalog/hooks/catalog-hooks", () => ({
  useItems: () => ({
    data: [
      { id: "item-1", name: "Milk", brand: null, barcode: null, image_url: null, stock_mode: "counted", default_expiration_days: null, created_at: "2026-02-14T00:00:00.000Z" },
    ],
  }),
  useLocations: () => ({ data: [{ id: "loc-1", name: "Main Pantry", description: null, created_at: "2026-02-14T00:00:00.000Z" }] }),
}));

describe("Phase 21 dashboard Quick Log", () => {
  beforeEach(() => {
    postMock.mockClear();
    window.localStorage.setItem("pantry_selected_location_id", "loc-1");
  });

  afterEach(() => {
    cleanup();
  });

  it("renders the kiosk dashboard panels and the Quick Log console", async () => {
    const { DashboardPage } = await import("./modules/inventory/pages/DashboardPage");
    renderWithProviders(<DashboardPage />, { initialEntries: ["/"] });

    expect(await screen.findByText("The Kitchen")).toBeInTheDocument();
    expect(screen.getByText("Quick Log")).toBeInTheDocument();
    expect(screen.getByText("Expiring soon")).toBeInTheDocument();
    expect(screen.getByText("Running low")).toBeInTheDocument();
    expect(screen.getByText("Cook tonight — from what you have")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Log as used/ })).toBeInTheDocument();
  });

  it("orders Running low by urgency (highest priority first)", async () => {
    const { DashboardPage } = await import("./modules/inventory/pages/DashboardPage");
    renderWithProviders(<DashboardPage />, { initialEntries: ["/"] });

    const urgent = await screen.findByText("Urgent item");
    const low = screen.getByText("Low urgency item");
    // Urgent (priority 3) must precede low (priority 1) in the DOM.
    expect(urgent.compareDocumentPosition(low) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  });

  it("logs a consumption when a Quick Log chip is tapped (remove, reason: consumption)", async () => {
    const user = userEvent.setup();
    const { DashboardPage } = await import("./modules/inventory/pages/DashboardPage");
    renderWithProviders(<DashboardPage />, { initialEntries: ["/"] });

    const chip = await screen.findByRole("button", { name: /Milk/ });
    await user.click(chip);

    await waitFor(() => expect(postMock).toHaveBeenCalled());
    const [url, body] = postMock.mock.calls[0];
    expect(url).toBe("/api/inventory/remove");
    expect(body).toMatchObject({
      item_id: "item-1",
      location_id: "loc-1",
      quantity: 1,
      reason: "consumption",
    });
  });
});
