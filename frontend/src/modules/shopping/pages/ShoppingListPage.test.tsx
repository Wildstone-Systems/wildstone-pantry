import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { renderWithProviders } from "@/test-utils";

const apiGet = vi.fn();
const apiPost = vi.fn();
const apiPatch = vi.fn();

vi.mock("@/api/client", () => ({
  api: {
    get: (...a: unknown[]) => apiGet(...a),
    post: (...a: unknown[]) => apiPost(...a),
    patch: (...a: unknown[]) => apiPatch(...a),
    delete: vi.fn(),
  },
}));

describe("ShoppingListPage", () => {
  beforeEach(() => {
    apiGet.mockReset();
    apiPost.mockReset();
    apiPatch.mockReset();
  });

  it("shows auto entries and adds a manual item", async () => {
    const user = userEvent.setup();
    apiGet.mockResolvedValue({
      data: {
        items: [
          { id: "s1", item_id: "i1", name: "Milk", quantity: 2, unit: "each", source: "auto", status: "pending", note: null, on_hand: 1, created_at: "", updated_at: "" },
        ],
      },
    });
    apiPost.mockResolvedValue({ data: { id: "s2", item_id: null, name: "Paper towels", quantity: 1, unit: "each", source: "manual", status: "pending", note: null, on_hand: null, created_at: "", updated_at: "" } });

    const { ShoppingListPage } = await import("./ShoppingListPage");
    renderWithProviders(<ShoppingListPage />, { initialEntries: ["/shopping-list"] });

    await waitFor(() => expect(screen.getByText(/Milk · 2 each/)).toBeInTheDocument());
    expect(screen.getByText("To buy (1)")).toBeInTheDocument();

    await user.type(screen.getByLabelText("Add an item"), "Paper towels");
    await user.click(screen.getByRole("button", { name: "Add" }));

    await waitFor(() => expect(apiPost).toHaveBeenCalledWith("/api/shopping-list", expect.objectContaining({ name: "Paper towels" })));
  });
});
