import { screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { renderWithProviders } from "@/test-utils";

import { FeatureGate } from "./FeatureGate";

const apiGet = vi.fn();
vi.mock("@/api/client", () => ({ api: { get: (...a: unknown[]) => apiGet(...a) } }));

function mockEntitlement(features: string[]) {
  apiGet.mockResolvedValue({
    data: {
      mode: "free",
      tier: "free",
      features,
      all_features: ["core", "store_integrations"],
      valid: true,
      source: "local",
      reason: "free",
      integrations_brokered: false,
    },
  });
}

describe("FeatureGate", () => {
  it("renders children when the instance is entitled to the feature", async () => {
    mockEntitlement(["core"]);
    renderWithProviders(
      <FeatureGate feature="core">
        <div>unlocked content</div>
      </FeatureGate>,
    );
    expect(await screen.findByText("unlocked content")).toBeInTheDocument();
  });

  it("shows the fallback (not the children) when not entitled", async () => {
    mockEntitlement(["core"]); // no store_integrations in free mode
    renderWithProviders(
      <FeatureGate feature="store_integrations" fallback={<div>upgrade please</div>}>
        <div>premium content</div>
      </FeatureGate>,
    );
    expect(await screen.findByText("upgrade please")).toBeInTheDocument();
    expect(screen.queryByText("premium content")).not.toBeInTheDocument();
  });

  it("defaults to a premium hint when no fallback is given", async () => {
    mockEntitlement(["core"]);
    renderWithProviders(
      <FeatureGate feature="store_integrations">
        <div>premium content</div>
      </FeatureGate>,
    );
    await waitFor(() => expect(screen.getByText(/Premium feature/)).toBeInTheDocument());
    expect(screen.queryByText("premium content")).not.toBeInTheDocument();
  });
});
