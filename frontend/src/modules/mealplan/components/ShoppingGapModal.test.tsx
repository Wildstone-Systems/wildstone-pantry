import { cleanup, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { renderWithProviders } from "@/test-utils";

import { formatAmount } from "../hooks/mealplan-hooks";
import { ShoppingGapModal } from "./ShoppingGapModal";

const apiGet = vi.fn();
const apiPost = vi.fn();
const apiDelete = vi.fn();

vi.mock("@/api/client", () => ({
  api: {
    get: (...a: unknown[]) => apiGet(...a),
    post: (...a: unknown[]) => apiPost(...a),
    delete: (...a: unknown[]) => apiDelete(...a),
  },
}));

const GAP = {
  start: "2026-11-03",
  end: "2026-11-08",
  lines: [
    { key: "canon:sugar", label: "Sugar", unit: "cup", needed: "3", on_hand: "0", to_buy: "3", status: "short", meals: ["Cake"] },
    { key: "item:abc", label: "Saffron", unit: "pinch", needed: "1", on_hand: null, to_buy: null, status: "check", meals: ["Paella"] },
  ],
  excluded: [{ id: "x1", key: "canon:salt", label: "Salt" }],
};

function renderModal() {
  return renderWithProviders(<ShoppingGapModal opened start="2026-11-01" today="2026-11-03" onClose={() => {}} />);
}

describe("formatAmount", () => {
  it("trims decimals and shows unknowns", () => {
    expect(formatAmount("3.50", "cup")).toBe("3.5 cup");
    expect(formatAmount(2, "each")).toBe("2 each");
    expect(formatAmount(null, "g")).toBe("?");
  });
});

describe("ShoppingGapModal", () => {
  beforeEach(() => {
    for (const m of [apiGet, apiPost, apiDelete]) m.mockReset();
    apiGet.mockResolvedValue({ data: GAP });
  });
  afterEach(() => cleanup());

  it("asks for the week from today and lists what to buy", async () => {
    renderModal();
    expect(await screen.findByText("Sugar")).toBeInTheDocument();
    expect(screen.getByText("3 cup")).toBeInTheDocument();
    expect(screen.getByText("check amount")).toBeInTheDocument();
    expect(screen.getByText("Salt")).toBeInTheDocument();
    expect(apiGet).toHaveBeenCalledWith("/api/meal-plan/shopping-gap", {
      params: { start: "2026-11-01", today: "2026-11-03", days: 7 },
    });
  });

  it("pushes only the ticked lines (measurable ones start ticked)", async () => {
    apiPost.mockResolvedValue({ data: { added: ["Sugar"], already_on_list: [] } });
    renderModal();
    await screen.findByText("Sugar");

    await userEvent.click(screen.getByRole("button", { name: "Add 1 to shopping list" }));
    await waitFor(() =>
      expect(apiPost).toHaveBeenCalledWith("/api/meal-plan/shopping-gap/push", {
        start: "2026-11-01",
        today: "2026-11-03",
        keys: ["canon:sugar"],
        days: 7,
      }),
    );
    expect(await screen.findByText(/Added Sugar\./)).toBeInTheDocument();
  });

  it("adds a staple to the always-skip list and can undo one", async () => {
    apiPost.mockResolvedValue({ data: { id: "x2", key: "canon:sugar", label: "Sugar" } });
    apiDelete.mockResolvedValue({});
    renderModal();
    await screen.findByText("Sugar");

    await userEvent.click(screen.getAllByRole("button", { name: "Always skip" })[0]);
    await waitFor(() =>
      expect(apiPost).toHaveBeenCalledWith("/api/meal-plan/excluded-items", { key: "canon:sugar", label: "Sugar" }),
    );

    await userEvent.click(screen.getByRole("button", { name: "Stop skipping Salt" }));
    await waitFor(() => expect(apiDelete).toHaveBeenCalledWith("/api/meal-plan/excluded-items/x1"));
  });
});
