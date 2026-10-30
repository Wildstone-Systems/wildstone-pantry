import { cleanup, fireEvent, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { renderWithProviders } from "@/test-utils";

const apiGet = vi.fn();
const apiPost = vi.fn();
const apiPatch = vi.fn();

vi.mock("@/api/client", () => ({
  api: {
    get: (...a: unknown[]) => apiGet(...a),
    post: (...a: unknown[]) => apiPost(...a),
    patch: (...a: unknown[]) => apiPatch(...a),
    delete: vi.fn(),
  },
}));

const SESSION = {
  id: "hs1",
  retailer_key: "walmart",
  retailer_label: "Walmart",
  status: "active",
  started_at: "2026-07-30T12:00:00Z",
  completed_at: null,
  items: [
    {
      id: "hi1",
      shopping_list_item_id: "s1",
      item_id: "i1",
      name: "Whole Milk",
      quantity: "1",
      unit: "each",
      state: "pending",
      external_id: null,
      external_name: null,
      external_url: null,
      note: null,
      search_url: "https://www.walmart.com/search?q=Whole+Milk",
      search_query: "Whole Milk",
      suggested_location_id: null,
    },
  ],
};

describe("HandoffSessionPage", () => {
  beforeEach(() => {
    // jsdom doesn't implement scrollIntoView; Mantine's Combobox keyboard-nav
    // timer calls it on the active option.
    Element.prototype.scrollIntoView = Element.prototype.scrollIntoView ?? (() => {});
    apiGet.mockReset();
    apiPost.mockReset();
    apiPatch.mockReset();
    apiGet.mockImplementation((url: string) => {
      if (url === "/api/shopping-list/handoff/hs1") return Promise.resolve({ data: SESSION });
      if (url === "/api/locations") return Promise.resolve({ data: { locations: [] } });
      return Promise.resolve({ data: {} });
    });
  });
  afterEach(cleanup);

  async function renderPage() {
    const { HandoffSessionPage } = await import("./HandoffSessionPage");
    renderWithProviders(
      <Routes>
        <Route path="/shopping-list/handoff/:sessionId" element={<HandoffSessionPage />} />
      </Routes>,
      { initialEntries: ["/shopping-list/handoff/hs1"] },
    );
  }

  it("deep-links each item into the retailer's site and marks progress", async () => {
    const user = userEvent.setup();
    apiPatch.mockResolvedValue({ data: { ...SESSION.items[0], state: "added" } });
    await renderPage();

    await waitFor(() => expect(screen.getByText("Shopping at Walmart")).toBeInTheDocument());
    const link = screen.getByRole("link", { name: /Search “Whole Milk” at Walmart/ });
    expect(link).toHaveAttribute("href", "https://www.walmart.com/search?q=Whole+Milk");
    expect(link).toHaveAttribute("target", "_blank");

    await user.click(screen.getByRole("button", { name: "Added" }));
    await waitFor(() =>
      expect(apiPatch).toHaveBeenCalledWith("/api/shopping-list/handoff/hs1/items/hi1", { state: "added" }),
    );
  });

  it("confirms the trip: checked items become the recorded purchase", async () => {
    const user = userEvent.setup();
    apiPost.mockResolvedValue({
      data: { ...SESSION, status: "completed", purchased_count: 1, purchase_id: "p1" },
    });
    await renderPage();

    await waitFor(() => expect(screen.getByText("Shopping at Walmart")).toBeInTheDocument());
    await user.click(screen.getByRole("button", { name: "Finish shopping" }));
    await user.click(await screen.findByRole("checkbox", { name: /Whole Milk/ }));
    await user.click(screen.getByRole("button", { name: "Record trip" }));

    await waitFor(() =>
      expect(apiPost).toHaveBeenCalledWith("/api/shopping-list/handoff/hs1/complete", {
        purchased_item_ids: ["hi1"],
        location_id: null,
      }),
    );
  });

  it("preselects a checked row's location from suggested_location_id and reports overrides on complete", async () => {
    const user = userEvent.setup();
    apiGet.mockImplementation((url: string) => {
      if (url === "/api/shopping-list/handoff/hs1")
        return Promise.resolve({
          data: { ...SESSION, items: [{ ...SESSION.items[0], suggested_location_id: "loc1" }] },
        });
      if (url === "/api/locations")
        return Promise.resolve({
          data: {
            locations: [
              { id: "loc1", name: "Fridge" },
              { id: "loc2", name: "Pantry" },
            ],
          },
        });
      return Promise.resolve({ data: {} });
    });
    apiPost.mockResolvedValue({
      data: { ...SESSION, status: "completed", purchased_count: 1, purchase_id: "p1" },
    });
    await renderPage();

    await waitFor(() => expect(screen.getByText("Shopping at Walmart")).toBeInTheDocument());
    await user.click(screen.getByRole("button", { name: "Finish shopping" }));
    await user.click(await screen.findByRole("checkbox", { name: /Whole Milk/ }));

    const itemSelect = (await screen.findByRole("textbox", { name: "Location for Whole Milk" })) as HTMLInputElement;
    expect(itemSelect.value).toBe("Fridge");

    await user.click(itemSelect);
    await user.click(await screen.findByRole("option", { name: "Pantry" }));

    await user.click(screen.getByRole("button", { name: "Record trip" }));

    await waitFor(() =>
      expect(apiPost).toHaveBeenCalledWith("/api/shopping-list/handoff/hs1/complete", {
        purchased_item_ids: ["hi1"],
        location_id: null,
        item_locations: { hi1: "loc2" },
      }),
    );
  });

  it("prompts on return focus for the most recently opened item and wires the quick actions", async () => {
    const user = userEvent.setup();
    let currentState = "pending";
    apiGet.mockImplementation((url: string) => {
      if (url === "/api/shopping-list/handoff/hs1")
        return Promise.resolve({
          data: { ...SESSION, items: [{ ...SESSION.items[0], state: currentState }] },
        });
      if (url === "/api/locations") return Promise.resolve({ data: { locations: [] } });
      return Promise.resolve({ data: {} });
    });
    apiPatch.mockImplementation((_url: string, body: { state: string }) => {
      currentState = body.state;
      return Promise.resolve({ data: { ...SESSION.items[0], state: currentState } });
    });
    await renderPage();

    await waitFor(() => expect(screen.getByText("Shopping at Walmart")).toBeInTheDocument());
    await user.click(screen.getByRole("link", { name: /Search “Whole Milk” at Walmart/ }));
    await waitFor(() =>
      expect(apiPatch).toHaveBeenCalledWith("/api/shopping-list/handoff/hs1/items/hi1", { state: "opened" }),
    );
    await waitFor(() => expect(screen.getByText("opened")).toBeInTheDocument());

    expect(screen.queryByText(/Did you add Whole Milk to your basket\?/)).not.toBeInTheDocument();

    fireEvent(window, new Event("focus"));

    expect(await screen.findByText(/Did you add Whole Milk to your basket\?/)).toBeInTheDocument();

    const addedButtons = screen.getAllByRole("button", { name: "Added" });
    await user.click(addedButtons[0]);

    await waitFor(() =>
      expect(apiPatch).toHaveBeenCalledWith("/api/shopping-list/handoff/hs1/items/hi1", { state: "added" }),
    );
  });
});
