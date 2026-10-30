import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { renderWithProviders } from "@/test-utils";

const apiGet = vi.fn(async (..._args: unknown[]) => ({
  data: {
    profiles: [
      { id: "p1", display_name: "Alex", account_id: null, daily_calorie_target: null, has_pin: false, is_active: true, created_at: "" },
      { id: "p2", display_name: "Sam", account_id: null, daily_calorie_target: null, has_pin: false, is_active: true, created_at: "" },
    ],
  },
}));

vi.mock("@/api/client", () => ({
  api: { get: (...args: unknown[]) => apiGet(...args) },
}));

describe("ProfilePicker (kiosk identity)", () => {
  beforeEach(() => window.localStorage.clear());
  afterEach(() => window.localStorage.clear());

  it("selecting a person sets the active profile (persisted for attribution)", async () => {
    const user = userEvent.setup();
    const { ProfilePicker } = await import("./ProfilePicker");

    renderWithProviders(<ProfilePicker />);

    // Nothing selected yet.
    const trigger = await screen.findByRole("button", { name: /Who's using this\?/ });
    await user.click(trigger);

    // Pick a person.
    await user.click(await screen.findByText("Alex"));

    // The button reflects the selection and it's persisted for the axios header.
    await waitFor(() => expect(screen.getByRole("button", { name: /Alex/ })).toBeInTheDocument());
    expect(window.localStorage.getItem("pantry_active_profile_id")).toBe("p1");
  });
});
