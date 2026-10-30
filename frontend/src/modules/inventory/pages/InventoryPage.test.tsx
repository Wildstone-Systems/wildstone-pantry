import { cleanup, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { renderWithProviders } from "@/test-utils";

const apiGet = vi.fn();
const apiPost = vi.fn();

vi.mock("@/api/client", () => ({
  api: {
    get: (...a: unknown[]) => apiGet(...a),
    post: (...a: unknown[]) => apiPost(...a),
    delete: vi.fn(),
  },
}));

const LOCATIONS = [{ id: "loc-1", name: "Pantry", description: null, is_active: true, created_at: "2026-01-01T00:00:00Z" }];

// Newest first, mirroring the backend's default log ordering.
const TRANSACTIONS = [
  {
    transaction_id: "t2",
    item_id: "i2",
    item_name: "Milk",
    location_id: "loc-1",
    location_name: "Pantry",
    quantity_change: 2,
    unit: "each",
    transaction_type: "add",
    reason: "manual",
    note: null,
    created_at: "2026-07-30T10:00:00Z",
  },
  {
    transaction_id: "t1",
    item_id: "i1",
    item_name: "Apple Juice",
    location_id: "loc-1",
    location_name: "Pantry",
    quantity_change: -1,
    unit: "each",
    transaction_type: "consume",
    reason: "manual",
    note: null,
    created_at: "2026-07-29T10:00:00Z",
  },
];

function mockGetImplementation(url: string) {
  if (url === "/api/items") return Promise.resolve({ data: { items: [] } });
  if (url === "/api/locations") return Promise.resolve({ data: { locations: LOCATIONS } });
  if (url.startsWith("/api/inventory/lots")) return Promise.resolve({ data: { lots: [] } });
  if (url.startsWith("/api/inventory/waste")) return Promise.resolve({ data: { items: [] } });
  if (url.startsWith("/api/inventory/containers")) return Promise.resolve({ data: { containers: [] } });
  if (url.startsWith("/api/inventory/transactions")) return Promise.resolve({ data: { transactions: TRANSACTIONS } });
  if (url.startsWith("/api/inventory")) return Promise.resolve({ data: { items: [] } });
  return Promise.resolve({ data: {} });
}

describe("InventoryPage - Log table", () => {
  beforeEach(() => {
    apiGet.mockReset();
    apiPost.mockReset();
    apiGet.mockImplementation(mockGetImplementation);
    window.localStorage.clear();
  });
  afterEach(cleanup);

  async function renderLogTab() {
    const { InventoryPage } = await import("./InventoryPage");
    renderWithProviders(<InventoryPage />);
    await user.click(await screen.findByText("Log"));
    await screen.findByText("Milk");
  }

  const user = userEvent.setup();

  function dataRows() {
    // First row is the header row.
    return screen.getAllByRole("row").slice(1);
  }

  it("clicking the Time header re-sorts the log rows", async () => {
    await renderLogTab();

    // Default: newest first (Milk, then Apple Juice) — unchanged from the fetch order.
    let rows = dataRows();
    expect(within(rows[0]).getByText("Milk")).toBeInTheDocument();
    expect(within(rows[1]).getByText("Apple Juice")).toBeInTheDocument();

    await user.click(screen.getByText("Time"));

    await waitFor(() => {
      const after = dataRows();
      expect(within(after[0]).getByText("Apple Juice")).toBeInTheDocument();
      expect(within(after[1]).getByText("Milk")).toBeInTheDocument();
    });
  });

  it("opens the transaction detail modal on row click and reverses via the endpoint", async () => {
    apiPost.mockResolvedValue({ data: { transaction_id: "t2", reversed_id: "t3" } });
    await renderLogTab();

    const rows = dataRows();
    await user.click(within(rows[0]).getByText("Milk"));

    expect(await screen.findByText("Transaction detail")).toBeInTheDocument();
    expect(screen.getByText(/Adds a compensating entry/)).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Reverse this entry" }));

    await waitFor(() => expect(apiPost).toHaveBeenCalledWith("/api/inventory/transactions/t2/reverse"));
    await waitFor(() => expect(screen.queryByText("Transaction detail")).not.toBeInTheDocument());
  });

  it("shows the server's 409 detail in the modal instead of dismissing it", async () => {
    apiPost.mockRejectedValue({
      response: { status: 409, data: { detail: "Not enough remaining stock to reverse this entry" } },
    });
    await renderLogTab();

    const rows = dataRows();
    await user.click(within(rows[0]).getByText("Milk"));
    await screen.findByText("Transaction detail");

    await user.click(screen.getByRole("button", { name: "Reverse this entry" }));

    expect(await screen.findByText("Not enough remaining stock to reverse this entry")).toBeInTheDocument();
    // The modal stays open so the message doesn't vanish like a toast would.
    expect(screen.getByText("Transaction detail")).toBeInTheDocument();
  });

  it("disables reversal for a reversal entry itself", async () => {
    apiGet.mockImplementation((url: string) => {
      if (url.startsWith("/api/inventory/transactions")) {
        return Promise.resolve({
          data: {
            transactions: [
              { ...TRANSACTIONS[0], transaction_id: "t3", reason: "reversal" },
              TRANSACTIONS[1],
            ],
          },
        });
      }
      return mockGetImplementation(url);
    });
    await renderLogTab();

    const rows = dataRows();
    await user.click(within(rows[0]).getByText("Milk"));
    await screen.findByText("Transaction detail");

    expect(screen.getByText("This is a reversal entry.")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Reverse this entry" })).toBeDisabled();
  });
});
