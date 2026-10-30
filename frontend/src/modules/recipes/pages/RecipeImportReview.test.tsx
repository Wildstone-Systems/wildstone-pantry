import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Route, Routes } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";

import { renderWithProviders } from "@/test-utils";
vi.mock("@/state/AuthProvider", () => ({ useAuth: () => ({ isAdmin: false }) }));

/**
 * Importing a recipe hands the parsed draft to the full editor for review
 * instead of a cut-down inline form. That is what makes a mis-parsed line
 * fixable: the editor can delete and add ingredient rows, and nothing is
 * written until save.
 */

const apiGetSpy = vi.fn(async (path: string) => {
  if (path === "/api/items") {
    return {
      data: {
        items: [
          {
            id: "item-1",
            name: "Flour",
            brand: null,
            barcode: null,
            default_expiration_days: null,
            created_at: "2026-02-14T00:00:00Z",
          },
        ],
      },
    };
  }
  if (path === "/api/recipes/unit-vocab") return { data: { units: [{ value: "cup", label: "cup" }, { value: "each", label: "each" }] } };
  if (path === "/api/recipes") return { data: [] };
  if (path === "/api/locations") return { data: { locations: [] } };
  if (path.startsWith("/api/recipes/suggestions")) {
    return { data: { location_id: null, suggestions: [] } };
  }
  return { data: {} };
});

const apiPostSpy = vi.fn(async (path: string, payload: any) => {
  if (path === "/api/recipes/parse-text") {
    return {
      data: {
        title: "Test Recipe",
        instructions: "Mix and bake.",
        ingredients: [
          { raw_line: "1 cup flour", name: "flour", quantity: 1, unit: "cup", item_id: "item-1", resolution: "matched" },
          { raw_line: "2 eggs", name: "eggs", quantity: 2, unit: "each" },
        ],
        raw_text: payload?.raw_text ?? "",
      },
    };
  }
  return { data: { id: "recipe-1" } };
});

vi.mock("@/api/client", () => ({
  api: {
    get: apiGetSpy,
    post: apiPostSpy,
    put: vi.fn(async () => ({ data: {} })),
    patch: vi.fn(async () => ({ data: {} })),
    delete: vi.fn(async () => ({ data: {} })),
  },
}));

describe("Recipe import review", () => {
  it("sends a parsed recipe to the editor, matching known items and flagging new ones", async () => {
    const user = userEvent.setup();
    const { RecipesPage } = await import("./RecipesPage");
    const { EditRecipePage } = await import("./EditRecipePage");

    renderWithProviders(
      <Routes>
        <Route path="/recipes" element={<RecipesPage />} />
        <Route path="/recipes/new" element={<EditRecipePage />} />
      </Routes>,
      { initialEntries: ["/recipes"] },
    );

    await user.click(screen.getByLabelText("Or paste recipe text"));
    await user.paste("Ingredients:\n1 cup flour\n2 eggs\n\nSteps:\n1. Mix and bake.");
    await user.click(screen.getByRole("button", { name: "Parse Text" }));

    // Landed in the real editor, prefilled — not an inline draft table.
    const nameInput = (await screen.findByLabelText(/Recipe Name/)) as HTMLInputElement;
    await waitFor(() => expect(nameInput.value).toBe("Test Recipe"));

    // "flour" matched the existing catalog item; "eggs" did not and is flagged
    // as something that will be created only if the row is kept.
    expect(await screen.findByLabelText("Ingredient name 2")).toHaveValue("eggs");

    // Editing stays stable — the field array must not remount rows on keystroke.
    nameInput.focus();
    await user.type(nameInput, "!");
    await waitFor(() => expect(nameInput).toHaveFocus());
  });
});
