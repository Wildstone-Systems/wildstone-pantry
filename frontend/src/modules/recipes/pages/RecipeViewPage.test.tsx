import { cleanup, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, it, vi } from "vitest";
import { renderWithProviders } from "@/test-utils";
import { RecipeViewPage } from "./RecipeViewPage";
const cook = vi.fn();
vi.mock("@/api/client", () => ({
  api: {
    get: async () => ({
      data: {
        recipe: { id: "recipe", name: "Chili", servings: 2, instructions: "Simmer beans." },
        ingredients: [
          { ingredient_id: "ingredient", item_id: "item", item_name: "Beans", quantity_required: "1", unit: "cup" },
        ],
      },
    }),
  },
}));
vi.mock("react-router-dom", async () => ({
  ...(await vi.importActual("react-router-dom")),
  useParams: () => ({ recipeId: "recipe" }),
}));
vi.mock("../hooks/recipes-hooks", () => ({
  useCookRecipe: () => ({ mutateAsync: cook, isPending: false }),
  useRecipeAvailability: () => ({ data: [] }),
}));
vi.mock("../components/RecipePhoto", () => ({ RecipePhoto: () => null }));
vi.mock("../components/RecipeAvailabilityModal", () => ({
  RecipeAvailabilityModal: ({ recipe }: { recipe: { name: string } | null }) =>
    recipe ? <div>Reviewing ingredients for {recipe.name}</div> : null,
}));
afterEach(cleanup);
it("opens ingredient review from the recipe without cooking or consuming stock", async () => {
  const user = userEvent.setup();
  renderWithProviders(<RecipeViewPage />);
  await user.click(await screen.findByRole("button", { name: "Review ingredient matches" }));
  expect(screen.getByText("Reviewing ingredients for Chili")).toBeVisible();
  expect(cook).not.toHaveBeenCalled();
});
