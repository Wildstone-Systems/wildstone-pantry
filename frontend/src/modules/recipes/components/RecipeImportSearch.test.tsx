import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Route, Routes, useLocation } from "react-router";
import { expect, it, vi } from "vitest";
import { renderWithProviders } from "@/test-utils";
import { RecipeImportSearch } from "./RecipeImportSearch";

const { candidate, importSpy } = vi.hoisted(() => ({ candidate: { name: "Chipotle soup", instructions: "Mix well", servings: 4, ingredients: [
  { name: "smoked chipotle", quantity: 1.5, unit: "g", optional: false },
], create_missing_items: true, image_url: "https://example.test/soup.png", external_source: { source: "themealdb", id: "123", url: "https://example.test/source" } }, importSpy: vi.fn() }));
vi.mock("../hooks/recipes-hooks", () => ({
  useSearchExternalRecipes: () => ({ data: { results: [{ source: "themealdb", external_id: "123", name: "Chipotle soup", source_label: "TheMealDB" }], providers_active: ["themealdb"] }, isFetching: false }),
  useExternalRecipeDetail: () => ({ mutateAsync: async () => ({ recipe: candidate, image_url: candidate.image_url }), isPending: false }),
  useValidateRecipe: () => ({ mutateAsync: async () => ({ ingredients: [], would_upsert: false }), isPending: false }),
  useImportRecipe: () => ({ mutateAsync: importSpy }),
}));
function DraftDestination() {
  return <pre data-testid="draft">{JSON.stringify(useLocation().state)}</pre>;
}
it("routes online imports into the editable review with original source and provider metadata intact", async () => {
  const user = userEvent.setup();
  renderWithProviders(<Routes><Route path="/recipes" element={<RecipeImportSearch />} /><Route path="/recipes/new" element={<DraftDestination />} /></Routes>, { initialEntries: ["/recipes"] });
  await user.click(screen.getByText("Chipotle soup"));
  await user.click(await screen.findByRole("button", { name: "Review in editor" }));
  await waitFor(() => expect(screen.getByTestId("draft")).toBeInTheDocument());
  const { draft } = JSON.parse(screen.getByTestId("draft").textContent!);
  expect(draft.structured_source).toEqual(candidate);
  expect(draft.servings).toBe(4);
  expect(draft.ingredients[0]).toMatchObject({ name: "smoked chipotle", quantity: 1.5, source_line_id: "field:ingredients[0]" });
  expect(importSpy).not.toHaveBeenCalled();
});
