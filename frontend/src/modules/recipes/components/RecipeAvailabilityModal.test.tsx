import { cleanup, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { renderWithProviders } from "@/test-utils";
import type { Recipe } from "../hooks/recipes-hooks";

const confirm = vi.fn().mockResolvedValue(undefined);
vi.mock("@/modules/catalog", () => ({ useItems: () => ({ data: [] }) }));
vi.mock("@/state/AuthProvider", () => ({ useAuth: () => ({ isAdmin: false, account: null }) }));
vi.mock("../hooks/recipes-hooks", () => ({
  useConfirmIngredientItem: () => ({ mutateAsync: confirm, isPending: false }),
  useApproveSubstitution: () => ({ mutateAsync: vi.fn(), isPending: false }),
  useRecipeAvailability: () => ({
    data: [
      {
        ingredient_id: "ingredient",
        item_id: "source",
        item_name: "salted butter",
        canonical_ingredient: "butter salted",
        quantity_required: "1",
        unit: "each",
        on_hand: "0",
        status: "suggested",
        suggestions: [
          {
            item_id: "butter",
            item_name: "My butter",
            canonical_ingredient: "butter unsalted",
            canonical_provenance: "confirmed",
            canonical_version: 2,
            canonical_revision: 6,
            on_hand: "2",
          },
        ],
        substitution_suggestions: [],
      },
    ],
  }),
}));
afterEach(() => {
  cleanup();
  confirm.mockClear();
});

describe("RecipeAvailabilityModal identity review", () => {
  it("opens a separate ranked admin review while preserving manual and recipe-only actions", async () => {
    const user = userEvent.setup();
    const { RecipeAvailabilityModal } = await import("./RecipeAvailabilityModal");
    renderWithProviders(
      <RecipeAvailabilityModal
        recipe={{ id: "recipe", name: "Toast" } as Recipe}
        locationId="pantry"
        onClose={() => {}}
      />
    );
    expect(await screen.findByRole("button", { name: "Review an item for this recipe" })).toBeEnabled();
    expect(screen.getByRole("button", { name: "Match to an item I have…" })).toBeEnabled();
    await user.click(screen.getByRole("button", { name: "Rank ingredient identity candidates" }));
    await screen.findByText(/Sign in as an admin to evaluate candidates/);
    expect(confirm).not.toHaveBeenCalled();
  });
  it("warns about household impact and requires replacement consent before writing a suggested match", async () => {
    const user = userEvent.setup();
    const { RecipeAvailabilityModal } = await import("./RecipeAvailabilityModal");
    renderWithProviders(
      <RecipeAvailabilityModal
        recipe={{ id: "recipe", name: "Toast" } as Recipe}
        locationId="pantry"
        onClose={() => {}}
      />
    );
    await user.click(await screen.findByRole("button", { name: "Use it" }));
    await screen.findByText(/every recipe in your household/);
    expect(confirm).not.toHaveBeenCalled();
    expect(await screen.findByRole("button", { name: "Confirm global identity" })).toBeDisabled();
    await user.click(screen.getByLabelText("Replace the previously confirmed identity"));
    await user.click(screen.getByRole("button", { name: "Confirm global identity" }));
    await waitFor(() =>
      expect(confirm).toHaveBeenCalledWith({
        ingredientId: "ingredient",
        itemId: "butter",
        expectedCanonical: "butter unsalted",
        expectedRevision: 6,
        expectedIngredient: "butter salted",
        replaceConfirmed: true,
      })
    );
  });
});
