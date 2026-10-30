import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { renderWithProviders } from "@/test-utils";

const apiGet = vi.fn();
const apiPost = vi.fn();
const apiPatch = vi.fn();

vi.mock("@/api/client", () => ({
  api: {
    get: (...args: unknown[]) => apiGet(...args),
    post: (...args: unknown[]) => apiPost(...args),
    patch: (...args: unknown[]) => apiPatch(...args),
    interceptors: { response: { use: () => 0, eject: () => undefined } },
  },
}));

describe("Household admin page", () => {
  beforeEach(() => {
    apiGet.mockReset();
    apiPost.mockReset();
    apiPatch.mockReset();
  });

  it("lists people and creates a new person", async () => {
    const user = userEvent.setup();
    apiGet.mockImplementation(async (url: string) => {
      if (url === "/api/auth/me") return { data: { id: "a1", email: "admin@x", role: "admin", is_active: true, created_at: "" } };
      return { data: { profiles: [{ id: "p1", display_name: "Alex", account_id: null, daily_calorie_target: null, has_pin: false, is_active: true, created_at: "" }] } };
    });
    apiPost.mockResolvedValue({ data: { id: "p2", display_name: "Kiddo", account_id: null, daily_calorie_target: 1600, has_pin: false, is_active: true, created_at: "" } });

    const { HouseholdPage } = await import("./HouseholdPage");
    const { AuthProvider } = await import("@/state/AuthProvider");
    renderWithProviders(
      <AuthProvider>
        <HouseholdPage />
      </AuthProvider>,
      { initialEntries: ["/admin/household"] },
    );

    // Existing person renders.
    await waitFor(() => expect(screen.getByText("Alex")).toBeInTheDocument());

    // Create a new person.
    await user.type(screen.getByLabelText("Name"), "Kiddo");
    await user.click(screen.getByRole("button", { name: "Add person" }));

    await waitFor(() => expect(apiPost).toHaveBeenCalledTimes(1));
    expect(apiPost).toHaveBeenCalledWith(
      "/api/household/profiles",
      expect.objectContaining({ display_name: "Kiddo" }),
    );
  });
});
