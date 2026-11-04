import { cleanup, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { renderWithProviders } from "@/test-utils";

vi.mock("@/api/client", () => ({
  api: { post: vi.fn().mockResolvedValue({ data: {} }), get: vi.fn().mockResolvedValue({ data: {} }) },
}));

const empty = { data: [], isLoading: false, isFetching: false, refetch: () => {} };

vi.mock("../hooks/scoped-hooks", () => ({
  useScopedExpiring: () => empty,
  useScopedRestock: () => empty,
  useScopedInventory: () => empty,
  useScopedTrends: () => empty,
  useScopedRecipeSuggestions: () => ({
    ...empty,
    data: [
      { recipe_id: "r-photo", recipe_name: "Shakshuka", image_src: "/api/recipes/r-photo/image", available_score: 1, missing_items: [] },
      { recipe_id: "r-bare", recipe_name: "Plain Rice", image_src: null, available_score: 0.5, missing_items: ["Rice"] },
    ],
  }),
}));

vi.mock("@/modules/catalog/hooks/catalog-hooks", () => ({
  useItems: () => ({ data: [] }),
  useLocations: () => ({ data: [{ id: "loc-1", name: "Main Pantry", description: null, created_at: "2026-02-14T00:00:00.000Z" }] }),
}));

describe("Dashboard 'Cook tonight' cards", () => {
  beforeEach(() => window.localStorage.setItem("pantry_selected_location_id", "loc-1"));
  afterEach(cleanup);

  it("links each card to its recipe", async () => {
    const { DashboardPage } = await import("./DashboardPage");
    renderWithProviders(<DashboardPage />, { initialEntries: ["/"] });

    const card = await screen.findByRole("link", { name: /open recipe: shakshuka/i });
    expect(card).toHaveAttribute("href", "/recipes/r-photo");
    expect(screen.getByRole("link", { name: /open recipe: plain rice/i })).toHaveAttribute("href", "/recipes/r-bare");
  });

  it("shows the recipe's picture, with a placeholder when it has none", async () => {
    const { DashboardPage } = await import("./DashboardPage");
    renderWithProviders(<DashboardPage />, { initialEntries: ["/"] });

    const card = await screen.findByRole("link", { name: /open recipe: shakshuka/i });
    expect(card.querySelector("img")).toHaveAttribute("src", "/api/recipes/r-photo/image");

    const bare = screen.getByRole("link", { name: /open recipe: plain rice/i });
    expect(bare.querySelector("img")).toBeNull();
    expect(bare.querySelector(".pantry-photo")).not.toBeNull();
  });
});
