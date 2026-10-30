import { cleanup, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { renderWithProviders } from "@/test-utils";
vi.mock("@/state/AuthProvider", () => ({ useAuth: () => ({ isAdmin: false }) }));

const apiGet = vi.fn((_url: string) => Promise.resolve({ data: { items: [] } as unknown }));
const apiPost = vi.fn(async () => ({ data: { id: "recipe-1" } }));
const apiPut = vi.fn(async () => ({ data: {} }));

vi.mock("@/api/client", () => ({
  api: { get: apiGet, post: apiPost, put: apiPut },
}));

describe("EditRecipePage (RHF + Zod field array)", () => {
  beforeEach(() => {
    apiGet.mockReset();
    apiGet.mockImplementation(async (url: string) => ({ data: url.endsWith("unit-vocab") ? { units: [] } : { items: [] } }));
    apiPost.mockClear();
    apiPut.mockClear();
  });
  afterEach(cleanup);

  it("round-trips package counts without converting their g and kg display labels", async () => {
    const user = userEvent.setup();
    apiGet.mockImplementation(async (url: string) => {
      if (url === "/api/recipes/r1") return { data: {
        recipe: { id: "r1", name: "Package roast", servings: 2 },
        ingredients: [
          { item_id: "pork", quantity_required: "2", unit: "g", quantity_mode: "package", yield_basis: true },
          { item_id: "pork", quantity_required: "3", unit: "kg", quantity_mode: "package", yield_basis: true },
        ],
      } };
      if (url === "/api/items") return { data: { items: [{ id: "pork", name: "Pork" }] } };
      return { data: { units: [{ value: "g", label: "g" }, { value: "kg", label: "kg" }] } };
    });
    const { EditRecipePage } = await import("./EditRecipePage");
    renderWithProviders(<Routes><Route path="/recipes/:recipeId/edit" element={<EditRecipePage />} /></Routes>, { initialEntries: ["/recipes/r1/edit"] });
    await screen.findByDisplayValue("Package roast");
    await user.type(screen.getByLabelText(/Recipe Name/), " edited");
    await user.click(screen.getByRole("button", { name: "Save Recipe" }));
    await waitFor(() => expect(apiPut).toHaveBeenCalledWith("/api/recipes/r1", expect.objectContaining({ ingredients: [
      { item_id: "pork", quantity: 2, unit: "g", optional: false, quantity_mode: "package", yield_basis: true },
      { item_id: "pork", quantity: 3, unit: "kg", optional: false, quantity_mode: "package", yield_basis: true },
    ] })));
  });

  it("preserves distinct package and amount rows and yield flags during an ordinary edit", async () => {
    const user = userEvent.setup();
    apiGet.mockImplementation(async (url: string) => {
      if (url === "/api/recipes/r1") return { data: {
        recipe: { id: "r1", name: "Roast", instructions: "Cook.", servings: 2 },
        ingredients: [
          { item_id: "pork", quantity_required: "1", unit: "each", quantity_mode: "package", yield_basis: true },
          { item_id: "pork", quantity_required: "250", unit: "g", quantity_mode: "amount", yield_basis: false },
        ],
      } };
      if (url === "/api/items") return { data: { items: [{ id: "pork", name: "Pork" }] } };
      return { data: { units: [{ value: "each", label: "each" }, { value: "g", label: "g" }] } };
    });
    const { EditRecipePage } = await import("./EditRecipePage");
    renderWithProviders(<Routes><Route path="/recipes/:recipeId/edit" element={<EditRecipePage />} /></Routes>, { initialEntries: ["/recipes/r1/edit"] });
    await screen.findByDisplayValue("Roast");
    expect(screen.getAllByRole("textbox", { name: "ingredient quantity" })).toHaveLength(2);
    await user.type(screen.getByLabelText(/Recipe Name/), " supper");
    await user.click(screen.getByRole("button", { name: "Save Recipe" }));
    await waitFor(() => expect(apiPut).toHaveBeenCalledWith("/api/recipes/r1", expect.objectContaining({
      name: "Roast supper",
      ingredients: [
        { item_id: "pork", quantity: 1, unit: "each", optional: false, quantity_mode: "package", yield_basis: true },
        { item_id: "pork", quantity: 250, unit: "g", optional: false, quantity_mode: "amount", yield_basis: false },
      ],
    })));
  });

  it("requires a name, then creates the recipe on save", async () => {
    const user = userEvent.setup();
    const { EditRecipePage } = await import("./EditRecipePage");

    renderWithProviders(
      <Routes>
        <Route path="/recipes/:recipeId" element={<EditRecipePage />} />
      </Routes>,
      { initialEntries: ["/recipes/new"] },
    );

    const save = screen.getByRole("button", { name: "Save Recipe" });
    expect(save).toBeDisabled();

    await user.type(screen.getByLabelText(/Recipe Name/), "Pancakes");
    await waitFor(() => expect(save).toBeEnabled());

    await user.click(save);

    await waitFor(() => expect(apiPost).toHaveBeenCalledTimes(1));
    expect(apiPost).toHaveBeenCalledWith(
      "/api/recipes",
      expect.objectContaining({ name: "Pancakes", instructions: "" }),
    );
    // Ingredients belong in the same create request, including an empty list.
    expect(apiPut).not.toHaveBeenCalled();
  });

  it("saves existing string quantities while omitting an unused Add Ingredient row", async () => {
    // Regression: the API serializes Decimal fields as strings (e.g. "0.250"),
    // not numbers. Reported live as "the Save button is locked out" even after
    // fixing the (separate) unit-vocabulary block — the real cause was
    // quantity_required failing z.number() validation on every row.
    const user = userEvent.setup();
    apiGet.mockImplementation(async (url: string) => {
      if (url === "/api/recipes/r1") {
        return {
          data: {
            recipe: { id: "r1", name: "Chili", instructions: "Simmer.", servings: 6, output_item_id: null, output_quantity: null, output_unit: null },
            ingredients: [{ ingredient_id: "i1", item_id: "item-1", item_name: "Beans", quantity_required: "0.250", unit: "cup", optional: false, quantity_mode: null, yield_basis: null }],
          },
        };
      }
      if (url === "/api/items") return { data: { items: [{ id: "item-1", name: "Beans" }] } };
      if (url === "/api/recipes/unit-vocab") return { data: { units: [{ value: "cup", label: "cup", dimension: "volume" }, { value: "each", label: "each", dimension: "count" }] } };
      return { data: { items: [] } };
    });
    const { EditRecipePage } = await import("./EditRecipePage");

    renderWithProviders(
      <Routes>
        <Route path="/recipes/:recipeId/edit" element={<EditRecipePage />} />
      </Routes>,
      { initialEntries: ["/recipes/r1/edit"] },
    );

    await waitFor(() => expect(screen.getByDisplayValue("Chili")).toBeInTheDocument());
    const save = screen.getByRole("button", { name: "Save Recipe" });
    await waitFor(() => expect(save).toBeEnabled());

    await user.click(screen.getByRole("button", { name: "+ Add Ingredient" }));
    expect(screen.getAllByRole("textbox", { name: "ingredient quantity" })).toHaveLength(2);
    await user.click(save);
    await waitFor(() => expect(apiPut).toHaveBeenCalledWith("/api/recipes/r1", expect.objectContaining({ ingredients: [
      expect.objectContaining({ item_id: "item-1", quantity: 0.25, unit: "cup" }),
    ] })));
  });
});
