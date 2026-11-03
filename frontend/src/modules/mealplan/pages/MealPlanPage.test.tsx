import { cleanup, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { renderWithProviders } from "@/test-utils";

import { addDays, sundayOf, todayLocal } from "../hooks/mealplan-hooks";

const apiGet = vi.fn();
const apiPost = vi.fn();
const apiPatch = vi.fn();
const apiDelete = vi.fn();

vi.mock("@/api/client", () => ({
  api: {
    get: (...a: unknown[]) => apiGet(...a),
    post: (...a: unknown[]) => apiPost(...a),
    patch: (...a: unknown[]) => apiPatch(...a),
    delete: (...a: unknown[]) => apiDelete(...a),
  },
}));

const SUNDAY = sundayOf(todayLocal());

function entry(over: Record<string, unknown>) {
  return {
    id: "e1",
    plan_date: SUNDAY,
    meal_type: "dinner",
    position: 0,
    kind: "recipe",
    meal: "Baked Turkey",
    recipe_id: "r1",
    item_id: null,
    title: "Baked Turkey",
    servings: 6,
    profile_id: null,
    status: "planned",
    cook_event_id: null,
    source: "manual",
    note: null,
    created_by: null,
    created_at: "",
    updated_at: "",
    ...over,
  };
}

const PROFILES = {
  profiles: [{ id: "p1", display_name: "Sam", account_id: null, daily_calorie_target: null, has_pin: false, is_active: true, created_at: "" }],
};

describe("helpers", () => {
  it("finds the Sunday that starts the week", () => {
    expect(sundayOf("2026-11-04")).toBe("2026-11-01");
    expect(sundayOf("2026-11-01")).toBe("2026-11-01");
    expect(sundayOf("2026-11-07")).toBe("2026-11-01");
    expect(addDays("2026-10-31", 1)).toBe("2026-11-01");
  });
});

describe("MealPlanPage", () => {
  beforeEach(() => {
    for (const m of [apiGet, apiPost, apiPatch, apiDelete]) m.mockReset();
    apiGet.mockImplementation(async (url: string) => {
      if (url === "/api/meal-plan/entries")
        return {
          data: {
            start: SUNDAY,
            days: 7,
            entries: [
              entry({}),
              entry({ id: "e2", position: 1, meal: "Green Bean Casserole", recipe_id: "r2", servings: 4 }),
              entry({ id: "e3", meal_type: "snack", kind: "text", meal: "Protein shake", recipe_id: null, servings: 1, profile_id: "p1" }),
            ],
          },
        };
      if (url === "/api/household/profiles") return { data: PROFILES };
      if (url === "/api/recipes") return { data: [{ id: "r1", name: "Baked Turkey", instructions: null, created_at: "" }] };
      if (url === "/api/items") return { data: { items: [] } };
      throw new Error(`unexpected GET ${url}`);
    });
  });
  afterEach(() => cleanup());

  it("shows a Sunday-start week with several meals per cell and who a meal is for", async () => {
    const { MealPlanPage } = await import("./MealPlanPage");
    renderWithProviders(<MealPlanPage />, { initialEntries: ["/meal-plan"] });

    const sunday = await screen.findByTestId(`day-${SUNDAY}`);
    expect(within(sunday).getByText("Sunday")).toBeInTheDocument();
    expect(await within(sunday).findByText("Baked Turkey")).toBeInTheDocument();
    expect(within(sunday).getByText("Green Bean Casserole")).toBeInTheDocument();
    expect(within(sunday).getByText("Protein shake")).toBeInTheDocument();
    expect(within(sunday).getByText("Sam")).toBeInTheDocument();
    expect(apiGet).toHaveBeenCalledWith("/api/meal-plan/entries", { params: { start: SUNDAY, days: 7 } });
  });

  it("moves to the next week", async () => {
    const { MealPlanPage } = await import("./MealPlanPage");
    renderWithProviders(<MealPlanPage />, { initialEntries: ["/meal-plan"] });
    await screen.findByTestId(`day-${SUNDAY}`);

    await userEvent.click(screen.getByRole("button", { name: "Next week" }));
    await waitFor(() =>
      expect(apiGet).toHaveBeenCalledWith("/api/meal-plan/entries", { params: { start: addDays(SUNDAY, 7), days: 7 } }),
    );
  });

  it("adds a free-text meal to an empty slot", async () => {
    apiPost.mockResolvedValue({ data: entry({ id: "e9", kind: "text", meal: "Takeout" }) });
    const { MealPlanPage } = await import("./MealPlanPage");
    renderWithProviders(<MealPlanPage />, { initialEntries: ["/meal-plan"] });
    await screen.findByTestId(`day-${SUNDAY}`);

    await userEvent.click(screen.getByRole("button", { name: "Add lunch on Sunday" }));
    await userEvent.click(await screen.findByText("Other"));
    await userEvent.type(screen.getByLabelText("What's planned"), "Takeout");
    await userEvent.click(screen.getByRole("button", { name: "Add to plan" }));

    await waitFor(() =>
      expect(apiPost).toHaveBeenCalledWith("/api/meal-plan/entries", {
        plan_date: SUNDAY,
        meal_type: "lunch",
        servings: null,
        profile_id: null,
        note: null,
        title: "Takeout",
      }),
    );
  });

  it("skips a planned meal from its editor", async () => {
    apiPost.mockResolvedValue({ data: entry({ status: "skipped" }) });
    const { MealPlanPage } = await import("./MealPlanPage");
    renderWithProviders(<MealPlanPage />, { initialEntries: ["/meal-plan"] });

    await userEvent.click(await screen.findByRole("button", { name: "Edit Baked Turkey" }));
    await userEvent.click(await screen.findByRole("button", { name: "Skip" }));
    await waitFor(() => expect(apiPost).toHaveBeenCalledWith("/api/meal-plan/entries/e1/skip"));
  });
});
