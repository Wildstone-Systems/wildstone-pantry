import { cleanup, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { renderWithProviders } from "@/test-utils";

import type { Recipe } from "../hooks/recipes-hooks";
import { RecipeCardGrid, recipeBucket } from "./RecipeCardGrid";

const recipe = (id: string, name: string, ingredient_count = 4): Recipe =>
  ({
    id,
    name,
    instructions: null,
    servings: 2,
    output_item_id: null,
    output_quantity: null,
    output_unit: null,
    created_at: "2026-01-01",
    ingredient_count,
    image_src: null,
  }) as Recipe;

const recipes = [recipe("a", "Chili"), recipe("b", "Tacos"), recipe("c", "Lasagna"), recipe("d", "Empty", 0)];
const scores: Record<string, number> = { a: 1, b: 0.6, c: 0.2 };
const missing: Record<string, string[]> = { a: [], b: ["salsa"], c: ["ricotta", "noodles", "basil"] };

const renderGrid = (onCookAlong = vi.fn()) =>
  renderWithProviders(
    <RecipeCardGrid
      recipes={recipes}
      scoreFor={(id) => scores[id] ?? null}
      missingFor={(id) => missing[id] ?? []}
      onOpen={vi.fn()}
      onCookAlong={onCookAlong}
      renderActions={() => null}
    />
  );

const cardNames = () => screen.getAllByTestId("recipe-card").map((c) => within(c).getAllByText(/./)[1].textContent);

describe("RecipeCardGrid", () => {
  afterEach(cleanup);

  it("buckets recipes by how many ingredients are missing", () => {
    expect(recipeBucket(1, 0, 3)).toBe("ready");
    expect(recipeBucket(0.5, 2, 3)).toBe("almost");
    expect(recipeBucket(0.1, 3, 3)).toBe("shop");
    expect(recipeBucket(null, 0, 0)).toBeNull();
  });

  it("features a cookable recipe as tonight's pick", async () => {
    const onCookAlong = vi.fn();
    renderGrid(onCookAlong);
    const pick = screen.getByLabelText("Tonight's pick");
    expect(within(pick).getByText("Chili")).toBeInTheDocument();
    await userEvent.click(within(pick).getByRole("button", { name: "Start cooking" }));
    expect(onCookAlong).toHaveBeenCalledWith(expect.objectContaining({ id: "a" }));
  });

  it("filters, searches and sorts by best match", async () => {
    const user = userEvent.setup();
    renderGrid();
    expect(screen.getAllByTestId("recipe-card")).toHaveLength(4);
    expect(cardNames()[0]).toBe("Chili");

    await user.click(screen.getByText("Missing 1–2 (1)"));
    expect(screen.getAllByTestId("recipe-card")).toHaveLength(1);
    expect(screen.getByText("Need: salsa")).toBeInTheDocument();

    await user.click(screen.getByText("All (4)"));
    await user.type(screen.getByLabelText("Search recipes"), "lasa");
    expect(screen.getAllByTestId("recipe-card")).toHaveLength(1);

    await user.click(screen.getByText("Can make now (1)"));
    expect(screen.queryByTestId("recipe-card")).toBeNull();
    expect(screen.getByText("No recipes match this filter.")).toBeInTheDocument();
  });
});
