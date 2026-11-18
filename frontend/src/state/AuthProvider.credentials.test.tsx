import { cleanup, render, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { getDeviceKey, getToken, setDeviceKey, setToken } from "@/auth";

import { AuthProvider } from "./AuthProvider";

// Synthetic credentials, built at runtime (no key-shaped literals).
const deviceKey = ["dk_", "tablet"].join("");
const loginToken = ["login", "-token"].join("");

let onRejected: ((error: unknown) => Promise<never>) | undefined;

vi.mock("@/api/client", () => ({
  api: {
    get: vi.fn(() => Promise.reject(new Error("offline"))),
    interceptors: {
      response: {
        use: (_ok: unknown, bad: (error: unknown) => Promise<never>) => {
          onRejected = bad;
          return 1;
        },
        eject: () => undefined,
      },
    },
  },
}));

const rejected = (sent: string) => ({ response: { status: 401, headers: {} }, config: { headers: { Authorization: sent } } });

describe("AuthProvider credential handling", () => {
  beforeEach(() => {
    window.localStorage.clear();
    onRejected = undefined;
  });
  afterEach(cleanup);

  it("an expired admin login does not unpair the device", async () => {
    setDeviceKey(deviceKey);
    setToken(loginToken);
    render(<AuthProvider>ok</AuthProvider>);
    await waitFor(() => expect(onRejected).toBeDefined());

    await onRejected!(rejected(`Bearer ${loginToken}`)).catch(() => undefined);
    expect(getToken()).toBeNull();
    expect(getDeviceKey()).toBe(deviceKey);
  });

  it("a revoked device key is cleared", async () => {
    setDeviceKey(deviceKey);
    render(<AuthProvider>ok</AuthProvider>);
    await waitFor(() => expect(onRejected).toBeDefined());

    await onRejected!(rejected(`Bearer ${deviceKey}`)).catch(() => undefined);
    expect(getDeviceKey()).toBeNull();
  });
});
