import { cleanup, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { renderWithProviders } from "@/test-utils";

import type { MealPlanEntry } from "../hooks/mealplan-hooks";
import { PlannedCookPrompt } from "./PlannedCookPrompt";

const apiPost = vi.fn();
vi.mock("@/api/client", () => ({ api: { post: (...a: unknown[]) => apiPost(...a) } }));

function entry(over: Partial<MealPlanEntry>): MealPlanEntry {
  return {
    id: "e1", plan_date: "2026-11-03", meal_type: "dinner", position: 0, kind: "recipe", meal: "Chilli",
    recipe_id: "r1", item_id: null, title: "Chilli", servings: 4, profile_id: null, status: "planned",
    cook_event_id: null, source: "manual", note: null, created_by: null, created_at: "", updated_at: "",
    ...over,
  };
}

describe("PlannedCookPrompt", () => {
  beforeEach(() => apiPost.mockReset());
  afterEach(() => cleanup());

  it("lets you pick which of several planned meals it was", async () => {
    apiPost.mockResolvedValue({ data: entry({ id: "e2", status: "cooked" }) });
    const onDone = vi.fn();
    renderWithProviders(
      <PlannedCookPrompt
        cookEventId="c1"
        candidates={[entry({}), entry({ id: "e2", meal_type: "lunch", servings: 2 })]}
        onDone={onDone}
      />,
    );
    expect(await screen.findByText(/planned more than once today/)).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Tuesday's lunch (×2)" }));
    await waitFor(() =>
      expect(apiPost).toHaveBeenCalledWith("/api/meal-plan/entries/e2/cooked", { cook_event_id: "c1" }),
    );
    expect(onDone).toHaveBeenCalled();
  });
});
