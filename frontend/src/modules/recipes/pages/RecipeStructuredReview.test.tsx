import { cleanup, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Link, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { renderWithProviders } from "@/test-utils";
const auth = vi.hoisted(() => ({ isAdmin: false }));
vi.mock("@/state/AuthProvider", () => ({ useAuth: () => auth }));

const post = vi.fn<(...args: unknown[]) => Promise<{ data: unknown }>>();
const put = vi.fn();
vi.mock("@/api/client", () => ({ api: {
  get: async (url: string) => ({ data: url === "/api/items" ? { items: [{ id: "backend-id", name: "Whole wheat flour" }, { id: "browser-id", name: "flour" }] } : url.endsWith("unit-vocab") ? { units: [{ value: "cup", label: "cup" }, { value: "each", label: "each" }] } : {} }),
  post, put,
} }));
beforeEach(() => { post.mockReset(); post.mockImplementation(async (...args: unknown[]) => ({ data: args[0] === "/api/recipes/import-preview" ? { binding: "current-hash", lines: [], annotations: [], status: "rules", warning: "Verify source" } : { id: "recipe-1" } })); put.mockClear(); });
afterEach(() => { cleanup(); auth.isAdmin = false; });

async function review(unit = "each", withSourceIds = false) {
  const { EditRecipePage } = await import("./EditRecipePage");
  const user = userEvent.setup();
  const draft = { title: "Bread", instructions: "Bake", source: "image", source_filename: "bread.png", raw_text: "Original recipe", ingredients: [
    { name: "flour", item_id: "backend-id", quantity: 1.5, unit: "cup", raw_line: "1 1/2 cups flour", resolution: "matched", optional: false, quantity_mode: "amount", yield_basis: true },
    { name: "salt", quantity: 1, unit, raw_line: "salt to taste", resolution: "would_create", optional: true, suggestion: "Check ingredient", quantity_mode: null, yield_basis: null, ...(withSourceIds ? { source_line_id: "line:2" } : {}) },
  ] };
  renderWithProviders(<Routes><Route path="/" element={<Link to="/recipes/new" state={{ draft }}>Review</Link>} /><Route path="/recipes/new" element={<EditRecipePage />} /></Routes>, { initialEntries: ["/"] });
  await user.click(screen.getByRole("link", { name: "Review" }));
  await screen.findByDisplayValue("Bread");
  return user;
}

it("keeps backend identity and source, then saves edited review in one structured request", async () => {
  const user = await review();
  expect(post).not.toHaveBeenCalled();
  expect(screen.getByText("Source: 1 1/2 cups flour")).toBeInTheDocument();
  expect(screen.getByText("Source file: bread.png")).toBeInTheDocument();
  expect(screen.getByText("Check ingredient")).toBeInTheDocument();
  expect(screen.getAllByLabelText("Optional / to taste")[1]).toBeChecked();
  const quantity = screen.getAllByRole("textbox", { name: "ingredient quantity" })[0];
  await user.clear(quantity);
  await user.type(quantity, "2.5");
  await user.click(screen.getByRole("button", { name: "+ Add Ingredient" }));
  await user.click(screen.getByRole("button", { name: "Check current recipe" }));
  await user.click(await screen.findByRole("checkbox", { name: /I checked the source/ }));
  await user.click(screen.getByRole("button", { name: "Save Recipe" }));
  await waitFor(() => expect(post).toHaveBeenCalledTimes(2));
  expect(post).toHaveBeenCalledWith("/api/recipes/import-reviewed", expect.objectContaining({ confirmed: true, reviewed_input_hash: "current-hash", recipe: expect.objectContaining({
    source: "image", source_filename: "bread.png", source_text: "Original recipe", create_missing_items: true,
    ingredients: [
      { item_id: "backend-id", quantity: 2.5, unit: "cup", optional: false, raw_line: "1 1/2 cups flour", quantity_mode: "amount", yield_basis: true },
      { name: "salt", quantity: 1, unit: "each", optional: true, raw_line: "salt to taste" },
    ],
  }) }));
  expect(put).not.toHaveBeenCalled();
});

it("binds padded titles and pending names to exactly the normalized recipe submitted on save", async () => {
  const user = await review("each", true);
  const title = screen.getByLabelText(/Recipe Name/);
  await user.clear(title);
  await user.type(title, "  Bread  ");
  const pending = screen.getByLabelText("Ingredient name 2");
  await user.clear(pending);
  await user.type(pending, "  smoked salt  ");
  let previewRecipe: unknown;
  let savedRecipe: unknown;
  post.mockImplementation(async (path, input) => {
    const packet = input as { recipe: unknown };
    if (path === "/api/recipes/import-preview") {
      previewRecipe = packet.recipe;
      return { data: { binding: "normalized-hash", lines: [], annotations: [], status: "rules", warning: "Verify source" } };
    }
    if (path === "/api/recipes/import-reviewed") {
      savedRecipe = packet.recipe;
      if (JSON.stringify(savedRecipe) !== JSON.stringify(previewRecipe)) throw new Error("409: Recipe changed; check again");
    }
    return { data: { id: "recipe-1" } };
  });
  await user.click(screen.getByRole("button", { name: "Check current recipe" }));
  await user.click(await screen.findByRole("checkbox", { name: /I checked the source/ }));
  await user.click(screen.getByRole("button", { name: "Save Recipe" }));
  expect(await screen.findByText("Recipe saved!")).toBeInTheDocument();
  expect(savedRecipe).toEqual(previewRecipe);
  expect(previewRecipe).toMatchObject({ name: "Bread", source_text: "Original recipe", ingredients: [
    expect.objectContaining({ optional: false, raw_line: "1 1/2 cups flour" }),
    expect.objectContaining({ name: "smoked salt", optional: true, raw_line: "salt to taste", source_line_id: "line:2" }),
  ] });
});

it("keeps invalid intermediate form values local and normalizes explicit model review too", async () => {
  auth.isAdmin = true;
  const user = await review();
  const title = screen.getByLabelText(/Recipe Name/);
  await user.clear(title);
  await user.type(title, "   ");
  expect(screen.getByRole("button", { name: "Check current recipe" })).toBeDisabled();
  const ask = screen.getByRole("button", { name: "Ask classifier for optional hints" });
  expect(ask).toBeDisabled();
  await user.click(ask);
  expect(post).not.toHaveBeenCalled();
  await user.clear(title);
  await user.type(title, "  Bread  ");
  post.mockResolvedValueOnce({ data: { binding: "model-hash", lines: [], annotations: [], status: "disabled", warning: "Verify source" } });
  await user.click(ask);
  await waitFor(() => expect(post).toHaveBeenCalledWith("/api/recipes/import-review", expect.objectContaining({
    recipe: expect.objectContaining({ name: "Bread", source_text: "Original recipe" }),
    source: { kind: "document", raw_text: "Original recipe", filename: "bread.png" },
  })));
});

it("blocks an unknown unit on a new ingredient before saving", async () => {
  const user = await review("pinch");
  expect(await screen.findByText(/Can't save yet/)).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Save Recipe" })).toBeDisabled();
  expect(post).not.toHaveBeenCalled();
  await user.click(screen.getByDisplayValue("pinch (unsupported)"));
  await user.click(screen.getByRole("option", { name: "each" }));
  expect(screen.getByRole("button", { name: "Save Recipe" })).toBeDisabled();
  await user.click(screen.getByRole("button", { name: "Check current recipe" }));
  await user.click(await screen.findByRole("checkbox", { name: /I checked the source/ }));
  await waitFor(() => expect(screen.getByRole("button", { name: "Save Recipe" })).toBeEnabled());
});

it("retains edits and source if the shared save fails", async () => {
  const user = await review();
  await user.type(screen.getByLabelText(/Recipe Name/), " edited");
  await user.click(screen.getByRole("button", { name: "Check current recipe" }));
  await user.click(await screen.findByRole("checkbox", { name: /I checked the source/ }));
  post.mockRejectedValueOnce(new Error("Failed to save recipe"));
  await user.click(screen.getByRole("button", { name: "Save Recipe" }));
  await screen.findByText("Failed to save recipe");
  expect(screen.getByDisplayValue("Bread edited")).toBeInTheDocument();
  expect(screen.getByText("Source: salt to taste")).toBeInTheDocument();
});

it("requires a new confirmation after editing a pending name and preserves immutable source", async () => {
  const user = await review();
  await user.click(screen.getByRole("button", { name: "Check current recipe" }));
  await user.click(await screen.findByRole("checkbox", { name: /I checked the source/ }));
  expect(screen.getByRole("button", { name: "Save Recipe" })).toBeEnabled();
  const pending = screen.getByLabelText("Ingredient name 2");
  await user.clear(pending);
  await user.type(pending, "smoked salt");
  expect(screen.getByRole("button", { name: "Save Recipe" })).toBeDisabled();
  expect(screen.queryByRole("checkbox", { name: /I checked the source/ })).not.toBeInTheDocument();
  await user.click(screen.getByRole("button", { name: "Check current recipe" }));
  expect(await screen.findByRole("checkbox", { name: /I checked the source/ })).not.toBeChecked();
  await user.click(screen.getByRole("checkbox", { name: /I checked the source/ }));
  await user.click(screen.getByRole("button", { name: "Save Recipe" }));
  await waitFor(() => expect(post).toHaveBeenCalledWith("/api/recipes/import-reviewed", expect.objectContaining({
    source: { kind: "document", raw_text: "Original recipe", filename: "bread.png" },
    recipe: expect.objectContaining({ ingredients: expect.arrayContaining([expect.objectContaining({ name: "smoked salt", raw_line: "salt to taste" })]) }),
  })));
  expect(post.mock.calls.some((args) => args[0] === "/api/items" || args[0] === "/api/recipes/import-review")).toBe(false);
});

it("discards a delayed review when the recipe changes during the request", async () => {
  const user = await review();
  let resolveReview!: (value: { data: unknown }) => void;
  post.mockImplementationOnce(() => new Promise((resolve) => { resolveReview = resolve as typeof resolveReview; }));
  await user.click(screen.getByRole("button", { name: "Check current recipe" }));
  await user.type(screen.getByLabelText(/Recipe Name/), " changed");
  resolveReview({ data: { binding: "old", lines: [], annotations: [], status: "rules", warning: "Old source" } });
  await waitFor(() => expect(screen.getByRole("button", { name: "Check current recipe" })).toBeEnabled());
  expect(screen.queryByText("Old source")).not.toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Save Recipe" })).toBeDisabled();
});

it("only requests optional classifier hints explicitly and keeps manual confirmation available when disabled", async () => {
  auth.isAdmin = true;
  const user = await review();
  expect(post).not.toHaveBeenCalled();
  post.mockResolvedValueOnce({ data: { binding: "manual-hash", status: "disabled", provider_requests: 0, warning: "Verify every suggestion", annotations: [], lines: [
    { source_line_id: "line:1", raw_line: "Mix well", line_index: 1, start: 10, end: 18, field_path: null, role: "instruction", warnings: ["This may be a cooking step; remove it from ingredients."] },
  ] } });
  await user.click(screen.getByRole("button", { name: "Ask classifier for optional hints" }));
  expect(await screen.findByText("Mix well")).toBeInTheDocument();
  expect(screen.getByText(/This may be a cooking step/)).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Save Recipe" })).toBeDisabled();
  await user.click(screen.getByRole("checkbox", { name: /I checked the source/ }));
  await user.click(screen.getByRole("button", { name: "Save Recipe" }));
  await waitFor(() => expect(post).toHaveBeenCalledWith("/api/recipes/import-reviewed", expect.objectContaining({ reviewed_input_hash: "manual-hash", confirmed: true })));
  expect(post.mock.calls.filter((args) => args[0] === "/api/recipes/import-review")).toHaveLength(1);
});
