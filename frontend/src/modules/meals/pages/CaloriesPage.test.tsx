import { cleanup, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { renderWithProviders } from "@/test-utils";

const apiGet = vi.fn();
const apiDelete = vi.fn();
const apiPatch = vi.fn();

vi.mock("@/api/client", () => ({
  api: { get: (...a: unknown[]) => apiGet(...a), delete: (...a: unknown[]) => apiDelete(...a), patch: (...a: unknown[]) => apiPatch(...a) },
}));

describe("CaloriesPage", () => {
  beforeEach(() => {
    apiGet.mockReset();
    apiDelete.mockReset();
    apiPatch.mockReset();
    window.localStorage.setItem("pantry_active_profile_id", "p1");
    apiGet.mockImplementation(async (url: string) => {
      if (url === "/api/household/profiles")
        return {
          data: {
            profiles: [{ id: "p1", display_name: "Alex", account_id: null, daily_calorie_target: 2000, has_pin: false, is_active: true, created_at: "" }],
          },
        };
      if (url === "/api/consumption/calories") {
        return {
          data: {
            profile_id: "p1",
            date: "2026-08-02",
            total_calories: "120",
            total_protein_g: null,
            total_fat_g: null,
            total_carbs_g: null,
            target: "2000",
            entries: [
              {
                event_id: "e1",
                item_name: "Organic Virgin Coconut Oil",
                quantity: "1",
                unit: "each",
                weight_grams: null,
                calories: "120",
                protein_g: null,
                fat_g: null,
                carbs_g: null,
                occurred_at: "2026-08-02T12:00:00Z",
              },
            ],
          },
        };
      }
      if (url === "/api/consumption/calories/summary") return { data: { date: "2026-08-02", profiles: [] } };
      return { data: {} };
    });
    apiDelete.mockResolvedValue({ data: null });
    apiPatch.mockResolvedValue({
      data: {
        event_id: "e2",
        item_id: "i1",
        item_name: "Organic Virgin Coconut Oil",
        location_id: null,
        profile_id: "p1",
        quantity: 2,
        unit: "each",
        calories: 240,
        protein_g: null,
        fat_g: null,
        carbs_g: null,
        balance: null,
      },
    });
  });
  afterEach(() => {
    cleanup();
    window.localStorage.clear();
  });

  it("deletes a mistaken entry after confirming", async () => {
    const user = userEvent.setup();
    const { CaloriesPage } = await import("./CaloriesPage");
    renderWithProviders(<CaloriesPage />, { initialEntries: ["/calories"] });

    await screen.findByText("Organic Virgin Coconut Oil");

    await user.click(screen.getByRole("button", { name: "Delete Organic Virgin Coconut Oil" }));
    await screen.findByText("Delete this entry?");
    await user.click(screen.getByRole("button", { name: "Delete" }));

    await waitFor(() => expect(apiDelete).toHaveBeenCalledWith("/api/consumption/plate/e1"));
  });

  it("cancelling the confirmation does not delete anything", async () => {
    const user = userEvent.setup();
    const { CaloriesPage } = await import("./CaloriesPage");
    renderWithProviders(<CaloriesPage />, { initialEntries: ["/calories"] });

    await screen.findByText("Organic Virgin Coconut Oil");
    await user.click(screen.getByRole("button", { name: "Delete Organic Virgin Coconut Oil" }));
    await screen.findByText("Delete this entry?");
    await user.click(screen.getByRole("button", { name: "Cancel" }));

    expect(apiDelete).not.toHaveBeenCalled();
  });

  it("edits an entry's quantity", async () => {
    const user = userEvent.setup();
    const { CaloriesPage } = await import("./CaloriesPage");
    renderWithProviders(<CaloriesPage />, { initialEntries: ["/calories"] });

    await screen.findByText("Organic Virgin Coconut Oil");
    await user.click(screen.getByRole("button", { name: "Edit Organic Virgin Coconut Oil" }));
    await screen.findByText("Edit Organic Virgin Coconut Oil");

    const qtyInput = screen.getByLabelText("Servings / qty");
    await user.clear(qtyInput);
    await user.type(qtyInput, "2");
    await user.click(screen.getByRole("button", { name: "Save" }));

    await waitFor(() =>
      expect(apiPatch).toHaveBeenCalledWith("/api/consumption/plate/e1", { quantity: 2, unit: "each", weight_grams: null }),
    );
  });

  it("pages back a day and asks the server for that date", async () => {
    const user = userEvent.setup();
    const { CaloriesPage } = await import("./CaloriesPage");
    renderWithProviders(<CaloriesPage />, { initialEntries: ["/calories"] });

    await screen.findByText("Organic Virgin Coconut Oil");
    const today = (await screen.findByLabelText("Date")) as HTMLInputElement;
    const todayValue = today.value;

    await user.click(screen.getByRole("button", { name: "Previous day" }));

    await waitFor(() =>
      expect(apiGet).toHaveBeenCalledWith(
        "/api/consumption/calories",
        expect.objectContaining({ params: expect.objectContaining({ on: expect.not.stringMatching(todayValue) }) }),
      ),
    );
    // The heading follows the selected day, not the wall clock.
    await screen.findByText(/Alex — Yesterday/);
  });

  it("cannot page into the future", async () => {
    const user = userEvent.setup();
    const { CaloriesPage } = await import("./CaloriesPage");
    renderWithProviders(<CaloriesPage />, { initialEntries: ["/calories"] });

    await screen.findByText("Organic Virgin Coconut Oil");
    // Starts on today, so forward is closed off — nothing can be eaten later.
    expect(screen.getByRole("button", { name: "Next day" })).toBeDisabled();

    await user.click(screen.getByRole("button", { name: "Previous day" }));
    await waitFor(() => expect(screen.getByRole("button", { name: "Next day" })).toBeEnabled());
  });

  it("offers a way straight back to today once you have navigated away", async () => {
    const user = userEvent.setup();
    const { CaloriesPage } = await import("./CaloriesPage");
    renderWithProviders(<CaloriesPage />, { initialEntries: ["/calories"] });

    await screen.findByText("Organic Virgin Coconut Oil");
    expect(screen.queryByRole("button", { name: "Today" })).toBeNull();

    await user.click(screen.getByRole("button", { name: "Previous day" }));
    await user.click(await screen.findByRole("button", { name: "Today" }));
    await screen.findByText(/Alex — Today/);
  });

  it("cancelling the edit modal does not save anything", async () => {
    const user = userEvent.setup();
    const { CaloriesPage } = await import("./CaloriesPage");
    renderWithProviders(<CaloriesPage />, { initialEntries: ["/calories"] });

    await screen.findByText("Organic Virgin Coconut Oil");
    await user.click(screen.getByRole("button", { name: "Edit Organic Virgin Coconut Oil" }));
    await screen.findByText("Edit Organic Virgin Coconut Oil");
    await user.click(screen.getByRole("button", { name: "Cancel" }));

    expect(apiPatch).not.toHaveBeenCalled();
  });
});
