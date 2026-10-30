import { cleanup, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { renderWithProviders } from "@/test-utils";

const apiGet = vi.fn();
const apiPost = vi.fn();

vi.mock("@/api/client", () => ({
  api: { get: (...a: unknown[]) => apiGet(...a), post: (...a: unknown[]) => apiPost(...a) },
}));
vi.mock("@/state/AuthProvider", () => ({ useAuth: () => ({ isAdmin: false, account: null }) }));

const MATCH = {
  recipe_id: "recipe1",
  ingredient_id: "ing1",
  item_id: "recipe-item",
  item_name: "coconut oil",
  canonical_ingredient: "coconut oil",
  recipe_names: ["Curry", "Granola"],
  candidates: [
    {
      item_id: "shelf1",
      item_name: "Organic Virgin Coconut Oil",
      canonical_ingredient: "coconut oil organic virgin",
      canonical_provenance: "generated",
      canonical_version: 2,
      canonical_revision: 0,
    },
  ],
};

describe("IngredientMatchBanner", () => {
  beforeEach(() => {
    apiGet.mockReset();
    apiPost.mockReset();
    apiGet.mockImplementation(async (url: string) => {
      if (url === "/api/recipes/ingredient-matches") return { data: { matches: [MATCH] } };
      return { data: {} };
    });
    apiPost.mockResolvedValue({ data: {} });
  });
  afterEach(cleanup);

  it("offers ranked review separately without evaluating or changing identity on opening", async () => {
    const user = userEvent.setup();
    const { IngredientMatchBanner } = await import("./IngredientMatchBanner");
    renderWithProviders(<IngredientMatchBanner />);
    await user.click(await screen.findByRole("button", { name: "Review" }));
    expect(await screen.findByRole("button", { name: "Rank identity candidates for coconut oil" })).toBeEnabled();
    expect(screen.getByRole("button", { name: "Same thing" })).toBeEnabled();
    await user.click(screen.getByRole("button", { name: "Rank identity candidates for coconut oil" }));
    await screen.findByText(/Sign in as an admin to evaluate candidates/);
    expect(apiPost).not.toHaveBeenCalled();
  });

  it("confirms a match, which resolves every recipe using that ingredient", async () => {
    const user = userEvent.setup();
    const { IngredientMatchBanner } = await import("./IngredientMatchBanner");
    renderWithProviders(<IngredientMatchBanner />);

    await screen.findByText(/1 ingredient may already be in your pantry/);
    await user.click(screen.getByRole("button", { name: "Review" }));

    // Shows which recipes it unblocks, so the payoff is visible before answering.
    await screen.findByText(/used in Curry, Granola/);

    await user.click(await screen.findByRole("button", { name: "Same thing" }));
    expect(apiPost).not.toHaveBeenCalled();
    await screen.findByText(/every recipe in your household/);
    await user.click(await screen.findByRole("button", { name: "Confirm global identity" }));
    await waitFor(() =>
      expect(apiPost).toHaveBeenCalledWith("/api/recipes/ingredients/ing1/confirm-item", {
        item_id: "shelf1",
        expected_canonical_ingredient: "coconut oil organic virgin",
        expected_canonical_revision: 0,
        expected_ingredient_key: "coconut oil",
        replace_confirmed: false,
      })
    );
  });

  it("requires deliberate replacement of a confirmed identity and supports cancelling without writes", async () => {
    apiGet.mockResolvedValue({
      data: {
        matches: [
          {
            ...MATCH,
            candidates: [{ ...MATCH.candidates[0], canonical_provenance: "confirmed", canonical_revision: 4 }],
          },
        ],
      },
    });
    const user = userEvent.setup();
    const { IngredientMatchBanner } = await import("./IngredientMatchBanner");
    renderWithProviders(<IngredientMatchBanner />);
    await user.click(await screen.findByRole("button", { name: "Review" }));
    await user.click(await screen.findByRole("button", { name: "Same thing" }));
    expect(await screen.findByRole("button", { name: "Confirm global identity" })).toBeDisabled();
    await user.click(screen.getByRole("button", { name: "Cancel identity change" }));
    expect(apiPost).not.toHaveBeenCalled();
    await user.click(await screen.findByRole("button", { name: "Same thing" }));
    await user.click(await screen.findByLabelText("Replace the previously confirmed identity"));
    await user.click(await screen.findByRole("button", { name: "Confirm global identity" }));
    await waitFor(() =>
      expect(apiPost).toHaveBeenCalledWith(
        expect.any(String),
        expect.objectContaining({ replace_confirmed: true, expected_canonical_revision: 4 })
      )
    );
  });

  it("renders nothing when there is nothing to reconcile", async () => {
    apiGet.mockImplementation(async () => ({ data: { matches: [] } }));
    const { IngredientMatchBanner } = await import("./IngredientMatchBanner");
    renderWithProviders(<IngredientMatchBanner />);
    await waitFor(() => expect(apiGet).toHaveBeenCalled());
    // No banner at all — not an empty one. (container.textContent is no good
    // here: the Mantine provider injects its stylesheet into the container.)
    expect(screen.queryByText(/may already be in your pantry/)).toBeNull();
    expect(screen.queryByRole("button", { name: "Review" })).toBeNull();
  });
});
