import { cleanup, screen } from "@testing-library/react";
import { afterEach, expect, it } from "vitest";
import { renderWithProviders } from "@/test-utils";
import { RecipeImportReceipt } from "./RecipeImportReceipt";
import type { Recipe } from "../hooks/recipes-hooks";
afterEach(cleanup);

it("keeps saved uncertainty and the original Unicode source link visible after save", () => {
  const recipe = { source_text: "🍲\nMix well", import_review: { stale: false, lines: [{
    source_line_id: "line:1", line_index: 1, start: 2, end: 10, field_path: null, role: "instruction", warnings: ["Check whether this belongs in the instructions."],
  }] } } as Recipe;
  renderWithProviders(<RecipeImportReceipt recipe={recipe} />);
  expect(screen.getByText("Mix well")).toBeInTheDocument();
  expect(screen.getByText("Source line 2")).toBeInTheDocument();
  expect(screen.getByText(/Confirmation does not guarantee/)).toBeInTheDocument();
  expect(screen.getByText(/Check whether this belongs/)).toBeInTheDocument();
  expect(screen.getByText(/human verification required/)).toBeInTheDocument();
});

it("warns about stale confirmation without applying old offsets to changed source", () => {
  renderWithProviders(<RecipeImportReceipt recipe={{ source_text: "Changed source", import_review: { stale: true, lines: [{
    source_line_id: "line:0", line_index: 0, start: 0, end: 7, field_path: null, role: "uncertain", warnings: ["Missing quantity"],
  }] } } as Recipe} />);
  expect(screen.getByText(/changed since confirmation/)).toBeInTheDocument();
  expect(screen.queryByText("Changed")).not.toBeInTheDocument();
});

it("does not invent a confirmation receipt for ordinary legacy recipes", () => {
  renderWithProviders(<RecipeImportReceipt recipe={{ name: "Manual recipe", import_review: null } as Recipe} />);
  expect(screen.queryByText("Imported recipe review history")).not.toBeInTheDocument();
});
