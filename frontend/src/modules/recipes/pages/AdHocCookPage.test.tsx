import { cleanup, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { renderWithProviders } from "@/test-utils";

const apiGet = vi.fn();
const apiPost = vi.fn();
const apiDelete = vi.fn();
const navigateMock = vi.fn();
const params: { recipeId?: string } = {};

vi.mock("@/api/client", () => ({
  api: {
    get: (...a: unknown[]) => apiGet(...a),
    post: (...a: unknown[]) => apiPost(...a),
    delete: (...a: unknown[]) => apiDelete(...a),
  },
}));

vi.mock("react-router-dom", async () => {
  const actual = await vi.importActual<typeof import("react-router-dom")>("react-router-dom");
  return { ...actual, useParams: () => params, useNavigate: () => navigateMock };
});

const DRAFT = {
  recipe: {
    id: "d1",
    name: "Untitled cook",
    instructions: null,
    servings: 1,
    created_at: "2026-08-11T10:00:00Z",
    is_draft: true,
  },
  ingredients: [],
};

const STATE_AFTER_ADD = {
  recipe: { ...DRAFT.recipe, instructions: "1. Fry the onion." },
  ingredients: [
    {
      ingredient_id: "ri1",
      item_id: "i-onion",
      item_name: "Onion",
      quantity_required: "2",
      unit: "each",
      optional: false,
    },
  ],
  flagged_units: [],
};

function mockGets() {
  apiGet.mockImplementation(async (url: string) => {
    if (url === "/api/recipes/ad-hoc") return { data: [] };
    if (url === "/api/recipes/d1") return { data: DRAFT };
    if (url === "/api/items") return { data: { items: [] } };
    if (url === "/api/recipes/unit-vocab")
      return {
        data: {
          units: [
            { value: "each", label: "each", dimension: "count" },
            { value: "g", label: "g", dimension: "mass" },
          ],
        },
      };
    return { data: {} };
  });
}

describe("AdHocCookPage", () => {
  beforeEach(() => {
    apiGet.mockReset();
    apiPost.mockReset();
    apiDelete.mockReset();
    navigateMock.mockReset();
    delete params.recipeId;
    mockGets();
  });
  afterEach(cleanup);

  it("starts a cook and lands on the capture screen", async () => {
    const user = userEvent.setup();
    apiPost.mockResolvedValue({ data: DRAFT });
    const { AdHocCookPage } = await import("./AdHocCookPage");
    renderWithProviders(<AdHocCookPage />, { initialEntries: ["/cook"] });

    await user.click(await screen.findByRole("button", { name: "Start cooking" }));

    await waitFor(() =>
      expect(apiPost).toHaveBeenCalledWith("/api/recipes/ad-hoc", { name: null }),
    );
    expect(navigateMock).toHaveBeenCalledWith("/cook/d1");
  });

  it("records an ingredient and a step as one entry", async () => {
    const user = userEvent.setup();
    params.recipeId = "d1";
    apiPost.mockResolvedValue({ data: STATE_AFTER_ADD });
    const { AdHocCookPage } = await import("./AdHocCookPage");
    renderWithProviders(<AdHocCookPage />, { initialEntries: ["/cook/d1"] });

    await user.type(await screen.findByLabelText("Ingredient"), "Onion");
    const qty = screen.getByLabelText("How much went in?");
    await user.clear(qty);
    await user.type(qty, "2");
    await user.type(screen.getByLabelText(/What did you do/), "Fry the onion.");
    await user.click(screen.getByRole("button", { name: "Add to cook" }));

    await waitFor(() =>
      expect(apiPost).toHaveBeenCalledWith("/api/recipes/d1/ad-hoc/entries", {
        ingredients: [{ item_id: null, name: "Onion", quantity: 2, unit: "each" }],
        step_text: "Fry the onion.",
      }),
    );

    // The response IS the new state: the merged line and numbered step render.
    await screen.findByText("2 each Onion");
    await screen.findByText("1. Fry the onion.");
  });

  it("stages several ingredients into one entry — a dressing is one action", async () => {
    const user = userEvent.setup();
    params.recipeId = "d1";
    apiPost.mockResolvedValue({ data: STATE_AFTER_ADD });
    const { AdHocCookPage } = await import("./AdHocCookPage");
    renderWithProviders(<AdHocCookPage />, { initialEntries: ["/cook/d1"] });

    // First ingredient staged, second left in the fields — both must post.
    await user.type(await screen.findByLabelText("Ingredient"), "Olive oil");
    await user.click(screen.getByRole("button", { name: "+ Another ingredient" }));
    await user.type(screen.getByLabelText("Ingredient"), "Vinegar");
    await user.type(screen.getByLabelText(/What did you do/), "Whisk into a dressing.");
    await user.click(screen.getByRole("button", { name: "Add to cook" }));

    await waitFor(() =>
      expect(apiPost).toHaveBeenCalledWith("/api/recipes/d1/ad-hoc/entries", {
        ingredients: [
          { item_id: null, name: "Olive oil", quantity: 1, unit: "each" },
          { item_id: null, name: "Vinegar", quantity: 1, unit: "each" },
        ],
        step_text: "Whisk into a dressing.",
      }),
    );
  });

  it("a staged ingredient can be removed before posting", async () => {
    const user = userEvent.setup();
    params.recipeId = "d1";
    const { AdHocCookPage } = await import("./AdHocCookPage");
    renderWithProviders(<AdHocCookPage />, { initialEntries: ["/cook/d1"] });

    await user.type(await screen.findByLabelText("Ingredient"), "Olive oil");
    await user.click(screen.getByRole("button", { name: "+ Another ingredient" }));
    await screen.findByText("1 each Olive oil");

    await user.click(screen.getByLabelText("Remove Olive oil"));
    expect(screen.queryByText("1 each Olive oil")).not.toBeInTheDocument();
  });

  it("finishes with save and shows the shortfall report instead of an error", async () => {
    const user = userEvent.setup();
    params.recipeId = "d1";
    apiGet.mockImplementation(async (url: string) => {
      if (url === "/api/recipes/d1") return { data: STATE_AFTER_ADD };
      if (url === "/api/items") return { data: { items: [] } };
      if (url === "/api/recipes/unit-vocab") return { data: { units: [] } };
      return { data: {} };
    });
    apiPost.mockResolvedValue({
      data: {
        recipe_id: "d1",
        saved: true,
        cook_logged: true,
        cook_event_id: "ce1",
        location_id: "loc1",
        deducted_transaction_ids: ["t1"],
        deducted: [{ item_id: "i-onion", item_name: "Onion", quantity: 1, unit: "each" }],
        shortfalls: [{ item_name: "Onion", short: 1, unit: "each" }],
        unconvertible: [],
      },
    });
    const { AdHocCookPage } = await import("./AdHocCookPage");
    renderWithProviders(<AdHocCookPage />, { initialEntries: ["/cook/d1"] });

    await user.click(await screen.findByRole("button", { name: "Finish cooking" }));
    await user.type(screen.getByLabelText("Recipe name"), "Fried onions");
    await user.click(screen.getByRole("button", { name: "Finish & log it" }));

    await waitFor(() =>
      expect(apiPost).toHaveBeenCalledWith("/api/recipes/d1/ad-hoc/finish", {
        servings_made: 2,
        save: true,
        name: "Fried onions",
      }),
    );

    // Deduct-to-zero (ADR 0024 §3): the gap is a report, not a refusal.
    await screen.findByText("Saved and logged");
    expect(screen.getByText(/short/)).toBeInTheDocument();
  });

  it("abandoning never posts a finish", async () => {
    const user = userEvent.setup();
    params.recipeId = "d1";
    apiDelete.mockResolvedValue({ data: {} });
    const { AdHocCookPage } = await import("./AdHocCookPage");
    renderWithProviders(<AdHocCookPage />, { initialEntries: ["/cook/d1"] });

    await user.click(await screen.findByRole("button", { name: "Discard" }));

    await waitFor(() => expect(apiDelete).toHaveBeenCalledWith("/api/recipes/d1"));
    expect(apiPost).not.toHaveBeenCalled();
  });
});
