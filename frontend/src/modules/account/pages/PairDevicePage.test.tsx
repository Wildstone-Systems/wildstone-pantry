import { cleanup, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { getDeviceKey, getToken } from "@/auth";
import { AuthProvider } from "@/state/AuthProvider";
import { renderWithProviders } from "@/test-utils";

// Keep the synthetic key readable without a key-like source literal.
const deviceKey = ["dk_", "tablet_", "123"].join("");

const apiGet = vi.fn();
const apiPost = vi.fn();

vi.mock("@/api/client", () => ({
  api: {
    get: (...args: unknown[]) => apiGet(...args),
    post: (...args: unknown[]) => apiPost(...args),
    interceptors: { response: { use: () => 0, eject: () => undefined } },
  },
  apiBaseUrl: () => "https://pantry.example.test",
}));

describe("Pair device page", () => {
  beforeEach(() => {
    apiGet.mockReset();
    apiPost.mockReset();
    window.localStorage.clear();
    apiGet.mockResolvedValue({ data: { id: "u1", email: "tablet@pantry", role: "member" } });
  });

  afterEach(cleanup);

  it("pairs with a code and stores the device key", async () => {
    const user = userEvent.setup();
    apiPost.mockResolvedValue({ data: { device_key: deviceKey } });
    const { PairDevicePage } = await import("./PairDevicePage");
    renderWithProviders(
      <AuthProvider>
        <PairDevicePage />
      </AuthProvider>,
      { initialEntries: ["/pair"] },
    );

    await user.type(screen.getByLabelText(/pairing code/i), " 123456-abcdef ");
    await user.click(screen.getByRole("button", { name: /pair device/i }));

    await waitFor(() => expect(getDeviceKey()).toBe(deviceKey));
    // Pairing is not an admin login.
    expect(getToken()).toBeNull();
    expect(apiPost).toHaveBeenCalledWith("/api/devices/pair", {
      code: "123456-abcdef",
      device_name: "Kitchen tablet",
    });
  });

  it("shows an error and stores nothing when the code is rejected", async () => {
    const user = userEvent.setup();
    apiPost.mockRejectedValue({ response: { status: 401, data: { detail: "Invalid or expired pairing code." } } });
    const { PairDevicePage } = await import("./PairDevicePage");
    renderWithProviders(
      <AuthProvider>
        <PairDevicePage />
      </AuthProvider>,
      { initialEntries: ["/pair"] },
    );

    await user.type(screen.getByLabelText(/pairing code/i), "000000-000000");
    await user.click(screen.getByRole("button", { name: /pair device/i }));

    expect(await screen.findByText(/invalid or expired pairing code/i)).toBeTruthy();
    expect(getDeviceKey()).toBeNull();
  });
});
