import { cleanup, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { renderWithProviders } from "@/test-utils";

const apiGet = vi.fn(async (url: string) => {
  if (url === "/api/items") return { data: { items: [] } };
  if (url === "/api/items/search-nutrition") {
    return {
      data: {
        throttled: false,
        searched_term: "banana",
        candidates: [
          { fdc_id: 1001, description: "Banana, raw", data_type: "Foundation", calories: 89, protein_g: 1.1, fat_g: 0.3, carbs_g: 22.8 },
        ],
      },
    };
  }
  return { data: {} };
});
const apiPost = vi.fn(async (_url: string, body: Record<string, unknown>) => ({
  data: { id: "new-1", is_active: true, created_at: "2026-07-23T00:00:00Z", ...body },
}));

vi.mock("@/api/client", () => ({
  api: { get: apiGet, post: apiPost },
}));

describe("AddItemPage", () => {
  beforeEach(() => {
    apiGet.mockClear();
    apiPost.mockClear();
  });

  afterEach(() => cleanup());

  it("includes per-100g nutrition in the create payload", async () => {
    const user = userEvent.setup();
    const { AddItemPage } = await import("./AddItemPage");
    renderWithProviders(<AddItemPage />, { initialEntries: ["/catalog/add"] });

    await user.type(await screen.findByLabelText(/Item name/), "Whole Milk");
    await user.type(await screen.findByLabelText(/^Calories$/), "60");
    await user.type(await screen.findByLabelText(/^Protein/), "3.2");
    await user.type(await screen.findByLabelText(/^Fat/), "3.3");
    await user.type(await screen.findByLabelText(/^Carbohydrates/), "4.8");

    await user.click(screen.getByRole("button", { name: "Create Item" }));

    await waitFor(() => expect(apiPost).toHaveBeenCalledTimes(1));
    expect(apiPost).toHaveBeenCalledWith(
      "/api/items",
      expect.objectContaining({
        name: "Whole Milk",
        energy_kcal_100g: 60,
        protein_g_100g: 3.2,
        fat_g_100g: 3.3,
        carbs_g_100g: 4.8,
      }),
    );
  });

  it("normalises a 0 in serving-size/density/weight to null (backend requires >0)", async () => {
    const user = userEvent.setup();
    const { AddItemPage } = await import("./AddItemPage");
    renderWithProviders(<AddItemPage />, { initialEntries: ["/catalog/add"] });

    await user.type(await screen.findByLabelText(/Item name/), "Zero Item");
    // A 0 here used to sail past the form and 422 the backend (gt=0).
    await user.type(await screen.findByLabelText(/^Serving size/), "0");
    await user.type(await screen.findByLabelText(/^Density/), "0");
    await user.type(await screen.findByLabelText(/^Weight per each/), "0");

    await user.click(screen.getByRole("button", { name: "Create Item" }));

    await waitFor(() => expect(apiPost).toHaveBeenCalledTimes(1));
    const body = apiPost.mock.calls[0][1];
    expect(body.serving_size_g).toBeNull();
    expect(body.density_g_per_ml).toBeNull();
    expect(body.grams_per_each).toBeNull();
  });

  it("sends an explicit tracking mode, and omits it when left on Auto", async () => {
    const user = userEvent.setup();
    const { AddItemPage } = await import("./AddItemPage");
    renderWithProviders(<AddItemPage />, { initialEntries: ["/catalog/add"] });

    await user.type(await screen.findByLabelText(/Item name/), "Milk");
    // Default is Auto → stock_mode omitted so the server infers it.
    await user.click(screen.getByRole("button", { name: "Create Item" }));
    await waitFor(() => expect(apiPost).toHaveBeenCalledTimes(1));
    expect(apiPost.mock.calls[0][1]).not.toHaveProperty("stock_mode");
  });

  it("prefills the barcode field from a ?barcode= query param carried from a failed scan", async () => {
    const { AddItemPage } = await import("./AddItemPage");
    renderWithProviders(<AddItemPage />, { initialEntries: ["/catalog/add?barcode=012345678905"] });

    expect(await screen.findByLabelText(/Barcode/)).toHaveValue("012345678905");
  });

  it("searches USDA, applies the pick as a preview, then re-applies it to the created item", async () => {
    // Bananas/produce have no barcode to auto-fill from — this is the path for
    // linking real nutrition to a manually created item at creation time.
    const user = userEvent.setup();
    const { AddItemPage } = await import("./AddItemPage");
    renderWithProviders(<AddItemPage />, { initialEntries: ["/catalog/add"] });

    await user.type(await screen.findByLabelText(/Item name/), "Banana");
    await user.click(screen.getByRole("button", { name: "Match to a USDA food…" }));

    await user.clear(await screen.findByLabelText("Search USDA foods"));
    await user.type(screen.getByLabelText("Search USDA foods"), "banana");

    await user.click(await screen.findByRole("button", { name: "Use this" }));

    // Preview applied locally before the item exists.
    expect(await screen.findByText(/USDA match:/)).toBeInTheDocument();
    expect(screen.getByLabelText(/^Calories$/)).toHaveValue("89 kcal");

    await user.click(screen.getByRole("button", { name: "Create Item" }));

    await waitFor(() => expect(apiPost).toHaveBeenCalledTimes(2));
    expect(apiPost).toHaveBeenNthCalledWith(
      1,
      "/api/items",
      expect.objectContaining({ name: "Banana", energy_kcal_100g: 89 }),
    );
    expect(apiPost).toHaveBeenNthCalledWith(
      2,
      "/api/items/new-1/nutrition-from-usda",
      expect.objectContaining({ fdc_id: 1001 }),
    );
  });
});
