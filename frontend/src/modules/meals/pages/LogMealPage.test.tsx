import { cleanup, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { renderWithProviders } from "@/test-utils";

const apiGet = vi.fn();
const apiPost = vi.fn();

vi.mock("@/api/client", () => ({
  api: { get: (...a: unknown[]) => apiGet(...a), post: (...a: unknown[]) => apiPost(...a) },
}));

const PROFILES = {
  profiles: [
    {
      id: "p1",
      display_name: "Alex",
      account_id: null,
      daily_calorie_target: 2000,
      has_pin: false,
      is_active: true,
      created_at: "",
    },
  ],
};

const BAR_UNITS = {
  item_id: "i1",
  item_name: "Protein Bar",
  units: [
    { unit: "each", grams: "60", calories: "240", protein_g: "20", fat_g: "8", carbs_g: "22" },
    { unit: "g", grams: "1", calories: "4", protein_g: null, fat_g: null, carbs_g: null },
  ],
  unresolvable: [{ unit: "cup", reason: "no density — set density_g_per_ml to convert a volume to grams" }],
  recommended_unit: "each",
  has_nutrition: true,
};

describe("LogMealPage", () => {
  beforeEach(() => {
    apiGet.mockReset();
    apiPost.mockReset();
    window.localStorage.clear();
    window.localStorage.setItem("pantry_active_profile_id", "p1");
    apiGet.mockImplementation(async (url: string) => {
      if (url === "/api/items")
        return { data: { items: [{ id: "i1", name: "Protein Bar", energy_kcal_100g: 400 }] } };
      if (url === "/api/locations") return { data: { locations: [] } };
      if (url === "/api/household/profiles") return { data: PROFILES };
      if (url === "/api/items/i1/loggable-units") return { data: BAR_UNITS };
      if (url === "/api/items/search-nutrition")
        return { data: { candidates: [], throttled: false, searched_term: "" } };
      return { data: {} };
    });
    apiPost.mockResolvedValue({
      data: { logged: [{ event_id: "e1" }], total_calories: 240, complete: true },
    });
  });
  afterEach(() => {
    cleanup();
    window.localStorage.clear();
  });

  it("one search box finds a pantry item and adds it to the pending meal", async () => {
    const user = userEvent.setup();
    const { LogMealPage } = await import("./LogMealPage");
    renderWithProviders(<LogMealPage />, { initialEntries: ["/log-meal"] });

    await user.type(await screen.findByLabelText("What did you eat?"), "protein");
    await user.click(await screen.findByText("in your pantry"));

    // Lands in the cart, not logged yet.
    await screen.findByLabelText("Amount of Protein Bar");
    expect(apiPost).not.toHaveBeenCalled();
  });

  it("defaults each line to a unit that actually converts, and previews the calories", async () => {
    const user = userEvent.setup();
    const { LogMealPage } = await import("./LogMealPage");
    renderWithProviders(<LogMealPage />, { initialEntries: ["/log-meal"] });

    await user.type(await screen.findByLabelText("What did you eat?"), "protein");
    await user.click(await screen.findByText("in your pantry"));

    // Server says "each" is the natural unit for a bar; the picker adopts it.
    await waitFor(() =>
      expect(screen.getAllByLabelText("Unit for Protein Bar")[0]).toHaveValue("each"),
    );
    // And the calories are visible BEFORE confirming.
    await screen.findByText("240 kcal");
  });

  it("logs the whole cart in one batch request", async () => {
    const user = userEvent.setup();
    const { LogMealPage } = await import("./LogMealPage");
    renderWithProviders(<LogMealPage />, { initialEntries: ["/log-meal"] });

    await user.type(await screen.findByLabelText("What did you eat?"), "protein");
    await user.click(await screen.findByText("in your pantry"));
    await waitFor(() => expect(screen.getAllByLabelText("Unit for Protein Bar")[0]).toHaveValue("each"));

    await user.click(screen.getByRole("button", { name: /^Log 1 item$/ }));

    await waitFor(() =>
      expect(apiPost).toHaveBeenCalledWith(
        "/api/consumption/plate-batch",
        expect.objectContaining({
          entries: [expect.objectContaining({ item_id: "i1", unit: "each", quantity: 1 })],
        }),
      ),
    );
    await screen.findByText(/Logged 1 item — 240 kcal/);
  });

  it("a typed barcode is offered from the same box (USB wedge scanners)", async () => {
    const user = userEvent.setup();
    apiPost.mockImplementation(async (url: string) => {
      if (url === "/api/barcodes/scan")
        return { data: { found: true, item_id: "i1", item_name: "Protein Bar" } };
      return { data: { logged: [], total_calories: 0, complete: true } };
    });
    const { LogMealPage } = await import("./LogMealPage");
    renderWithProviders(<LogMealPage />, { initialEntries: ["/log-meal"] });

    // Digits only -> offered as a barcode lookup, and Enter takes the top hit.
    await user.type(await screen.findByLabelText("What did you eat?"), "012345678905{enter}");

    await waitFor(() =>
      expect(apiPost).toHaveBeenCalledWith(
        "/api/barcodes/scan",
        expect.objectContaining({ barcode: "012345678905" }),
      ),
    );
    await screen.findByLabelText("Amount of Protein Bar");
  });

  it("keeps the pending meal across a reload — nothing is written until you log", async () => {
    window.localStorage.setItem(
      "pantry_meal_cart",
      JSON.stringify([{ key: "k1", itemId: "i1", itemName: "Protein Bar", quantity: 2, unit: "each", unitSeeded: true }]),
    );
    const { LogMealPage } = await import("./LogMealPage");
    renderWithProviders(<LogMealPage />, { initialEntries: ["/log-meal"] });

    await screen.findByLabelText("Amount of Protein Bar");
    expect(screen.getByRole("button", { name: /^Log 1 item$/ })).toBeInTheDocument();
    expect(apiPost).not.toHaveBeenCalled();
  });

  it("snaps a cart saved by the old stepper to what its wheels can show", async () => {
    // Half a bar survives — "each" has a quarter wheel. 156.7 g does not: grams
    // are whole, and logging a number the wheels cannot show is how the total
    // stops matching what's on screen.
    const user = userEvent.setup();
    window.localStorage.setItem(
      "pantry_meal_cart",
      JSON.stringify([
        { key: "k1", itemId: "i1", itemName: "Protein Bar", quantity: 1.5, unit: "each", unitSeeded: true },
        { key: "k2", itemId: "i1", itemName: "Protein Bar", quantity: 156.7, unit: "g", unitSeeded: true },
      ]),
    );
    const { LogMealPage } = await import("./LogMealPage");
    renderWithProviders(<LogMealPage />, { initialEntries: ["/log-meal"] });

    await screen.findAllByLabelText("Amount of Protein Bar");
    await user.click(screen.getByRole("button", { name: /^Log 2 items$/ }));

    await waitFor(() =>
      expect(apiPost).toHaveBeenCalledWith(
        "/api/consumption/plate-batch",
        expect.objectContaining({
          entries: [
            expect.objectContaining({ quantity: 1.5, unit: "each" }),
            expect.objectContaining({ quantity: 157, unit: "g" }),
          ],
        }),
      ),
    );
  });

  it("offers a quarter wheel for things you count, and none for grams", async () => {
    window.localStorage.setItem(
      "pantry_meal_cart",
      JSON.stringify([
        { key: "k1", itemId: "i1", itemName: "Protein Bar", quantity: 1, unit: "each", unitSeeded: true },
        { key: "k2", itemId: "i1", itemName: "Protein Bar", quantity: 150, unit: "g", unitSeeded: true },
      ]),
    );
    const { LogMealPage } = await import("./LogMealPage");
    renderWithProviders(<LogMealPage />, { initialEntries: ["/log-meal"] });

    // Half a bar is a real thing to eat; half a gram is not.
    await waitFor(() =>
      expect(screen.getAllByRole("spinbutton", { name: "fraction of Amount of Protein Bar" })).toHaveLength(1),
    );
  });

  it("the amount wheels move by place value — hundreds in one gesture", async () => {
    const user = userEvent.setup();
    window.localStorage.setItem(
      "pantry_meal_cart",
      JSON.stringify([{ key: "k1", itemId: "i1", itemName: "Protein Bar", quantity: 7, unit: "g", unitSeeded: true }]),
    );
    const { LogMealPage } = await import("./LogMealPage");
    renderWithProviders(<LogMealPage />, { initialEntries: ["/log-meal"] });

    // 7 g -> 157 g: one move per column, not 150 taps on a "+".
    await user.click(await screen.findByRole("spinbutton", { name: "hundreds of Amount of Protein Bar" }));
    await user.keyboard("15");

    await user.click(screen.getByRole("button", { name: /^Log 1 item$/ }));
    await waitFor(() =>
      expect(apiPost).toHaveBeenCalledWith(
        "/api/consumption/plate-batch",
        expect.objectContaining({ entries: [expect.objectContaining({ quantity: 157, unit: "g" })] }),
      ),
    );
  });
});
