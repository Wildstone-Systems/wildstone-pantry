import { cleanup, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { renderWithProviders } from "@/test-utils";

const apiGet = vi.fn();
const apiDelete = vi.fn();

vi.mock("@/api/client", () => ({
  api: { get: (...a: unknown[]) => apiGet(...a), delete: (...a: unknown[]) => apiDelete(...a) },
}));

import { PurgeItemModal } from "./PurgeItemModal";

const ITEM = { id: "i1", name: "Basmati Rice", is_active: false } as never;

describe("PurgeItemModal", () => {
  beforeEach(() => {
    apiGet.mockReset();
    apiDelete.mockReset();
    apiGet.mockResolvedValue({
      data: {
        item_id: "i1",
        name: "Basmati Rice",
        is_active: false,
        ledger_events: 16,
        lots: 2,
        on_hand: 3,
        recipe_ingredients: 4,
      },
    });
    apiDelete.mockResolvedValue({ data: { purged: true } });
  });
  afterEach(cleanup);

  it("states what will be destroyed, in counts rather than adjectives", async () => {
    renderWithProviders(<PurgeItemModal item={ITEM} onClose={() => {}} />);
    expect(await screen.findByText(/16 inventory events/)).toBeInTheDocument();
    expect(screen.getByText(/2 stock lots/)).toBeInTheDocument();
    expect(screen.getByText(/4 recipe ingredients/)).toBeInTheDocument();
    // And says plainly that this rewrites past totals.
    expect(screen.getByText(/Past totals .* will change/i)).toBeInTheDocument();
  });

  it("keeps the button disabled until the name is typed exactly", async () => {
    const user = userEvent.setup();
    renderWithProviders(<PurgeItemModal item={ITEM} onClose={() => {}} />);

    const confirm = await screen.findByRole("button", { name: /delete permanently/i });
    expect(confirm).toBeDisabled();

    const field = screen.getByLabelText(/Type "Basmati Rice" to confirm/);
    await user.type(field, "basmati rice"); // wrong case
    expect(confirm).toBeDisabled();

    await user.clear(field);
    await user.type(field, "Basmati Rice");
    await waitFor(() => expect(confirm).toBeEnabled());
  });

  it("sends the typed name as the confirmation the server checks", async () => {
    const user = userEvent.setup();
    const onClose = vi.fn();
    renderWithProviders(<PurgeItemModal item={ITEM} onClose={onClose} />);

    await user.type(await screen.findByLabelText(/to confirm/), "Basmati Rice");
    await user.click(screen.getByRole("button", { name: /delete permanently/i }));

    await waitFor(() =>
      expect(apiDelete).toHaveBeenCalledWith("/api/items/i1/purge", {
        params: { confirm_name: "Basmati Rice" },
      }),
    );
    await waitFor(() => expect(onClose).toHaveBeenCalled());
  });

  it("cancelling deletes nothing", async () => {
    const user = userEvent.setup();
    renderWithProviders(<PurgeItemModal item={ITEM} onClose={() => {}} />);
    await user.click(await screen.findByRole("button", { name: "Cancel" }));
    expect(apiDelete).not.toHaveBeenCalled();
  });
});
