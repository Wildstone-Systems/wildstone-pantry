import { cleanup, screen } from "@testing-library/react";
import { Route, Routes } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";

import { renderWithProviders } from "@/test-utils";

import { SetupGate } from "./SetupGate";

const apiGet = vi.fn();
vi.mock("@/api/client", () => ({ api: { get: (...a: unknown[]) => apiGet(...a) } }));

function mountAt(path: string) {
  return renderWithProviders(
    <Routes>
      <Route element={<SetupGate />}>
        <Route path="/" element={<div>the app</div>} />
      </Route>
      <Route path="/setup" element={<div>the wizard</div>} />
    </Routes>,
    { initialEntries: [path] },
  );
}

afterEach(cleanup);

describe("SetupGate", () => {
  it("redirects a fresh instance to the wizard", async () => {
    apiGet.mockResolvedValue({
      data: { completed: false, needs_admin: true, household_name: null, accounts_mode: false },
    });
    mountAt("/");
    expect(await screen.findByText("the wizard")).toBeInTheDocument();
  });

  it("renders the app when setup is completed", async () => {
    apiGet.mockResolvedValue({
      data: { completed: true, needs_admin: false, household_name: "The Halls", accounts_mode: false },
    });
    mountAt("/");
    expect(await screen.findByText("the app")).toBeInTheDocument();
  });

  it("never blocks on a failing status check (ADR 0018 §7)", async () => {
    apiGet.mockRejectedValue(new Error("backend cold"));
    mountAt("/");
    // Loading/error → assume set up; the app renders.
    expect(await screen.findByText("the app")).toBeInTheDocument();
  });
});
