import { cleanup, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { renderWithProviders } from "@/test-utils";

const apiPost = vi.fn();
const apiGet = vi.fn();

vi.mock("@/api/client", () => ({
  api: { post: (...a: unknown[]) => apiPost(...a), get: (...a: unknown[]) => apiGet(...a) },
}));

const LOT = {
  lot_id: "lot1",
  item_id: "i1",
  item_name: "Old Yogurt",
  location_id: "loc1",
  location_name: "Fridge",
  quantity_remaining: "2",
  unit: "each",
  status: "open",
  expiration_date: null,
  frozen: false,
  sealed_count: null,
  container_size: null,
};

const ITEM = { id: "i1", name: "Old Yogurt", size_description: "32 oz" };

describe("InventoryItemModal", () => {
  beforeEach(() => {
    apiPost.mockReset();
    apiGet.mockReset();
    apiGet.mockImplementation(async () => ({ data: { locations: [{ id: "loc1", name: "Fridge" }] } }));
    apiPost.mockResolvedValue({ data: {} });
  });
  afterEach(cleanup);

  it("throws an item away from the On hand modal — recorded as waste", async () => {
    // Previously this lived only behind the Lots & batches tab's Spoil action.
    const user = userEvent.setup();
    const onClose = vi.fn();
    const { InventoryItemModal } = await import("./InventoryItemModal");
    renderWithProviders(
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      <InventoryItemModal lot={LOT as any} item={ITEM as any} onClose={onClose} />,
    );

    await user.click(await screen.findByRole("button", { name: "Throw away Old Yogurt" }));
    // Destructive, so it confirms rather than firing on the first tap.
    expect(apiPost).not.toHaveBeenCalled();

    await user.click(await screen.findByRole("button", { name: "Yes, throw it away" }));
    await waitFor(() => expect(apiPost).toHaveBeenCalledWith("/api/inventory/lots/lot1/spoil"));
  });

  it("marks a lot used up — recorded as usage, no confirm step", async () => {
    // Unlike Spoil/Remove, this is the everyday good outcome, so it fires
    // straight away rather than confirming first — matches the Lots &
    // batches tab and the Track worklist's "finish" action.
    const user = userEvent.setup();
    const onClose = vi.fn();
    const { InventoryItemModal } = await import("./InventoryItemModal");
    renderWithProviders(
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      <InventoryItemModal lot={LOT as any} item={ITEM as any} onClose={onClose} />,
    );

    await user.click(await screen.findByRole("button", { name: "Mark Old Yogurt used up" }));
    await waitFor(() => expect(apiPost).toHaveBeenCalledWith("/api/inventory/lots/lot1/finish"));
    await waitFor(() => expect(onClose).toHaveBeenCalled());
  });

  it("keeps 'threw it away' and 'added by mistake' as separate actions", async () => {
    // They mean different things: one records waste, the other is a correction
    // that must NOT show up in spoilage trends.
    const user = userEvent.setup();
    const { InventoryItemModal } = await import("./InventoryItemModal");
    renderWithProviders(
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      <InventoryItemModal lot={LOT as any} item={ITEM as any} onClose={vi.fn()} />,
    );

    await user.click(await screen.findByRole("button", { name: "Remove — added by mistake" }));
    await user.click(await screen.findByRole("button", { name: "Yes, remove it" }));
    await waitFor(() => expect(apiPost).toHaveBeenCalledWith("/api/inventory/lots/lot1/remove"));
    expect(apiPost).not.toHaveBeenCalledWith("/api/inventory/lots/lot1/spoil");
  });

  it("cancelling the confirmation discards nothing", async () => {
    const user = userEvent.setup();
    const { InventoryItemModal } = await import("./InventoryItemModal");
    renderWithProviders(
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      <InventoryItemModal lot={LOT as any} item={ITEM as any} onClose={vi.fn()} />,
    );

    await user.click(await screen.findByRole("button", { name: "Throw away Old Yogurt" }));
    await user.click(await screen.findByRole("button", { name: "Keep it" }));
    expect(apiPost).not.toHaveBeenCalled();
  });
});
