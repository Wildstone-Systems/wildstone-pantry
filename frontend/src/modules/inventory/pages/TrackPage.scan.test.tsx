import { cleanup, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { renderWithProviders } from "@/test-utils";

/**
 * The merged scan lanes (ADR 0023 Phase 2).
 *
 * What matters here is that consuming a container-tracked item resolves to a
 * *container* rather than an amount, and that an ambiguous scan asks instead of
 * guessing — the prompt is the safety property the whole design rests on, so
 * these tests assert that nothing moves until a choice is made.
 */

const CONTAINER_PROMPT = {
  status: "needs_container",
  item_id: "item-yogurt",
  item_name: "Yogurt",
  inventory: [],
  resolved_location_id: "loc-1",
  resolved_location_name: "Fridge",
  candidate_locations: [],
  candidate_containers: [
    { lot_id: "lot-a", quantity_remaining: "0.5", unit: "qt", expiration_date: "2026-08-10", status: "open" },
    { lot_id: "lot-b", quantity_remaining: "0.8", unit: "qt", expiration_date: "2026-08-20", status: "open" },
  ],
  transaction_id: null,
};

const SCAN_DONE = {
  status: "done",
  item_id: "item-yogurt",
  item_name: "Yogurt",
  inventory: [{ quantity: "2", unit: "qt" }],
  resolved_location_id: "loc-1",
  resolved_location_name: "Fridge",
  candidate_locations: [],
  candidate_containers: [],
  transaction_id: "tx-out",
};

/** A catch-weight product: one barcode over packages that all weigh differently
 *  (ADR 0025 §1). The nominal 4.87 lb is a starting point to correct, not a fact. */
const CATCH_WEIGHT = {
  barcode: "0700000000007",
  found: true,
  item_id: "item-roast",
  item_name: "Pork Butt Roast",
  item: { size_quantity: 4.87, size_unit: "lb", variable_weight: true },
  product: null,
  location_candidates: [{ location_id: "loc-1", location_name: "Freezer", quantity: "0" }],
};

let scanResponse: unknown = SCAN_DONE;

const apiGet = vi.fn(async (url: string) => {
  if (url.startsWith("/api/locations")) {
    return { data: { locations: [{ id: "loc-1", name: "Fridge", description: null, is_active: true }] } };
  }
  if (url.includes("/api/inventory/worklist")) return { data: { location_id: "loc-1", rows: [] } };
  if (url === "/api/items/search-nutrition") {
    return {
      data: {
        throttled: false,
        searched_term: "banana",
        candidates: [
          { fdc_id: 2001, description: "Banana, raw", data_type: "Foundation", calories: 89, protein_g: 1.1, fat_g: 0.3, carbs_g: 22.8 },
        ],
      },
    };
  }
  if (url.startsWith("/api/items/")) {
    return { data: { id: "item-yogurt", name: "Yogurt", size_quantity: 1, size_unit: "qt" } };
  }
  if (url.startsWith("/api/items")) return { data: { items: [] } };
  if (url.includes("item-locations")) return { data: [] };
  return { data: {} };
});

const apiPost = vi.fn(async (url: string, body?: Record<string, unknown>) => {
  if (url === "/api/inventory/scan") return { data: scanResponse };
  if (url === "/api/barcodes/scan") {
    if (body?.barcode === "0300000000009") {
      return { data: { barcode: "0300000000009", found: false, item_id: null, item_name: null, item: null, product: null, location_candidates: [] } };
    }
    if (body?.barcode === "0700000000007") return { data: CATCH_WEIGHT };
    if (body?.barcode === "0500000000003") {
      return {
        data: {
          barcode: "0500000000003",
          found: true,
          item_id: "item-cereal",
          item_name: "Cereal",
          item: { size_quantity: 1, size_unit: "ea" },
          product: { name: "Cereal", brand: null, size_quantity: 1, size_unit: "ea" },
          location_candidates: [],
        },
      };
    }
    if (body?.barcode === "0600000000000") {
      return {
        data: {
          barcode: "0600000000000",
          found: true,
          item_id: "item-multi",
          item_name: "Multi",
          item: { size_quantity: 1, size_unit: "ea" },
          product: { name: "Multi", brand: null, size_quantity: 1, size_unit: "ea" },
          location_candidates: [
            { location_id: "loc-1", location_name: "Fridge", quantity: "2" },
            { location_id: "loc-2", location_name: "Pantry", quantity: "1" },
          ],
        },
      };
    }
    return {
      data: {
        barcode: "0200000000001",
        found: true,
        item_id: "item-yogurt",
        item_name: "Yogurt",
        item: { size_quantity: 1, size_unit: "qt" },
        product: { name: "Yogurt", brand: "Farmhouse", size_quantity: 1, size_unit: "qt" },
        location_candidates: [],
      },
    };
  }
  if (url === "/api/items") {
    return { data: { id: "item-banana", name: body?.name, barcode: body?.barcode ?? null, is_active: true, created_at: "2026-08-10T00:00:00Z" } };
  }
  if (url === "/api/items/item-banana/nutrition-from-usda") {
    return { data: { id: "item-banana", name: "Banana", energy_kcal_100g: 89, nutrition_source: "Banana, raw" } };
  }
  if (url === "/api/inventory/add") return { data: { transaction_ids: ["tx-in"] } };
  return { data: { transaction_id: "tx-1", already_resolved: false } };
});

vi.mock("@/api/client", () => ({
  api: {
    get: (url: string) => apiGet(url),
    post: (url: string, body?: Record<string, unknown>) => apiPost(url, body),
    put: (url: string, body?: Record<string, unknown>) => apiPost(url, body),
  },
}));

async function renderPage() {
  const { TrackPage } = await import("./TrackPage");
  renderWithProviders(<TrackPage />);
  await screen.findByLabelText(/Barcode or item name/i);
}

async function scan(user: ReturnType<typeof userEvent.setup>, barcode: string) {
  const field = await screen.findByLabelText(/Barcode or item name/i);
  await user.type(field, `${barcode}{Enter}`);
}

describe("TrackPage scan lanes", () => {
  beforeEach(() => {
    window.localStorage.clear();
    apiPost.mockClear();
    apiGet.mockClear();
    scanResponse = SCAN_DONE;
  });
  afterEach(() => {
    cleanup();
    window.localStorage.clear();
  });

  it("opens ready to take stock out, since a household mostly consumes", async () => {
    await renderPage();
    const out = screen.getByRole("radio", { name: /Out — used it/ }) as HTMLInputElement;
    expect(out.checked).toBe(true);
  });

  it("remembers the direction on this device", async () => {
    const user = userEvent.setup();
    await renderPage();

    await user.click(screen.getByRole("radio", { name: /In — putting away/ }));
    await waitFor(() => expect(window.localStorage.getItem("pantry_scan_direction")).toBe("in"));
  });

  it("scanning out consumes and offers undo", async () => {
    const user = userEvent.setup();
    await renderPage();

    await scan(user, "0200000000001");

    await waitFor(() => expect(apiPost).toHaveBeenCalledWith("/api/inventory/scan", expect.anything()));
    expect(await screen.findByRole("button", { name: "Undo" })).toBeInTheDocument();
  });

  it("asks which container when several are open, and moves nothing", async () => {
    const user = userEvent.setup();
    scanResponse = CONTAINER_PROMPT;
    await renderPage();

    await scan(user, "0200000000001");

    expect(await screen.findByText(/Which Yogurt did you finish\?/)).toBeInTheDocument();
    // A prompt is not a write: no undo is offered because nothing happened.
    expect(screen.queryByRole("button", { name: "Undo" })).not.toBeInTheDocument();
  });

  it("has no way to skip the container prompt", async () => {
    const user = userEvent.setup();
    scanResponse = CONTAINER_PROMPT;
    await renderPage();
    await scan(user, "0200000000001");
    await screen.findByText(/Which Yogurt did you finish\?/);

    // Only the two containers and an explicit Cancel — no "always the oldest",
    // no auto-pick. Guessing is what corrupts the surviving container's expiry.
    expect(screen.queryByRole("button", { name: /always/i })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /auto/i })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Cancel/ })).toBeInTheDocument();
  });

  it("answering the prompt re-scans against that container", async () => {
    const user = userEvent.setup();
    scanResponse = CONTAINER_PROMPT;
    await renderPage();
    await scan(user, "0200000000001");
    await screen.findByText(/Which Yogurt did you finish\?/);

    scanResponse = SCAN_DONE;
    await user.click(screen.getByRole("button", { name: /0.8 qt left/ }));

    await waitFor(() =>
      expect(apiPost).toHaveBeenCalledWith("/api/inventory/scan", expect.objectContaining({ lot_id: "lot-b" })),
    );
  });

  it("cancelling the prompt leaves the stock alone", async () => {
    const user = userEvent.setup();
    scanResponse = CONTAINER_PROMPT;
    await renderPage();
    await scan(user, "0200000000001");
    await screen.findByText(/Which Yogurt did you finish\?/);

    await user.click(screen.getByRole("button", { name: /Cancel/ }));

    await waitFor(() => expect(screen.queryByText(/Which Yogurt did you finish\?/)).not.toBeInTheDocument());
    const lotCalls = apiPost.mock.calls.filter((c) => (c[1] as Record<string, unknown>)?.lot_id);
    expect(lotCalls).toHaveLength(0);
  });

  it("won't check in until a location is chosen", async () => {
    const user = userEvent.setup();
    await renderPage();
    await user.click(screen.getByRole("radio", { name: /In — putting away/ }));
    await scan(user, "0200000000001");

    // Viewing "All locations", so there is no sensible default — stock has to
    // land somewhere specific, and guessing would put it in the wrong room.
    expect(await screen.findByRole("button", { name: /Add to inventory/ })).toBeDisabled();
  });

  it("checking in records containers, so the stock can be finished later", async () => {
    const user = userEvent.setup();
    await renderPage();
    await user.click(screen.getByRole("radio", { name: /In — putting away/ }));

    await scan(user, "0200000000001");
    await screen.findByRole("button", { name: /Add to inventory/ });

    await user.click(screen.getByLabelText(/intake location/i));
    await user.click(await screen.findByRole("option", { name: "Fridge" }));

    await user.click(screen.getByRole("button", { name: /Add to inventory/ }));

    await waitFor(() =>
      expect(apiPost).toHaveBeenCalledWith(
        "/api/inventory/add",
        expect.objectContaining({ sealed_count: 1, container_size: 1, container_unit: "qt" }),
      ),
    );
  });

  it("says plainly when intake has no package size to record", async () => {
    const user = userEvent.setup();
    await renderPage();
    await user.click(screen.getByRole("radio", { name: /In — putting away/ }));
    await scan(user, "0200000000001");
    await screen.findByRole("button", { name: /Add to inventory/ });

    // Clear the prefilled size: the user should be told what they lose.
    await user.clear(screen.getByLabelText(/Each contains/i));

    expect(await screen.findByText(/"Finished it" won't be available/)).toBeInTheDocument();
  });

  it("a barcode nothing matches can be created and USDA-matched right here, then checked in without a rescan", async () => {
    // The dead end this replaces: produce/deli items (bananas, a pork butt
    // roast) have no UPC a provider knows about, so the old flow sent the
    // household away to Catalog and back. This keeps them on Track.
    const user = userEvent.setup();
    await renderPage();
    await user.click(screen.getByRole("radio", { name: /In — putting away/ }));

    await scan(user, "0300000000009");
    expect(await screen.findByText(/Not in the catalog yet/)).toBeInTheDocument();

    await user.type(screen.getByLabelText("Item name"), "Banana");
    await user.click(screen.getByRole("button", { name: /Match to a USDA food…/ }));
    await user.click(await screen.findByRole("button", { name: "Use this" }));
    expect(await screen.findByText("Banana, raw")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Create item" }));

    await waitFor(() =>
      expect(apiPost).toHaveBeenCalledWith("/api/items", expect.objectContaining({ name: "Banana", barcode: "0300000000009" })),
    );
    await waitFor(() =>
      expect(apiPost).toHaveBeenCalledWith("/api/items/item-banana/nutrition-from-usda", expect.objectContaining({ fdc_id: 2001 })),
    );

    // Back to the normal check-in card for the item just created — no rescan.
    expect(await screen.findByRole("button", { name: /Add to inventory/ })).toBeInTheDocument();
  });

  it("scanning the next item commits the pending one automatically, the scan page's rapid-fire pattern", async () => {
    const user = userEvent.setup();
    await renderPage();
    await user.click(screen.getByRole("radio", { name: /In — putting away/ }));

    await scan(user, "0200000000001");
    await screen.findByRole("button", { name: /Add to inventory/ });
    await user.click(screen.getByLabelText(/intake location/i));
    await user.click(await screen.findByRole("option", { name: "Fridge" }));
    expect(screen.getByRole("button", { name: /Add to inventory/ })).not.toBeDisabled();

    apiPost.mockClear();
    await scan(user, "0500000000003");

    // The pending Yogurt committed before Cereal was looked up.
    await waitFor(() =>
      expect(apiPost).toHaveBeenCalledWith("/api/inventory/add", expect.objectContaining({ item_id: "item-yogurt" })),
    );
    expect(apiPost).toHaveBeenCalledWith("/api/barcodes/scan", { barcode: "0500000000003", auto_create: true });
    expect(await screen.findByText("Cereal")).toBeInTheDocument();
  });

  it("scanning while a location choice is still pending blocks the next scan instead of dropping it", async () => {
    const user = userEvent.setup();
    await renderPage();
    await user.click(screen.getByRole("radio", { name: /In — putting away/ }));

    await scan(user, "0600000000000");
    await screen.findByLabelText(/intake location/i);
    expect(screen.getByRole("button", { name: /Add to inventory/ })).toBeDisabled();

    apiPost.mockClear();
    await scan(user, "0200000000001");

    expect(await screen.findByText(/Finish checking in the current item first/)).toBeInTheDocument();
    expect(apiPost).not.toHaveBeenCalledWith("/api/inventory/add", expect.anything());
    expect(apiPost).not.toHaveBeenCalledWith("/api/barcodes/scan", expect.objectContaining({ barcode: "0200000000001" }));
    // Still the original, unresolved item — not silently replaced.
    expect(screen.getByText("Multi")).toBeInTheDocument();
  });

  it("a catch-weight item asks what THIS package weighs", async () => {
    // The whole bug in one screen: without this the operator taps through and
    // every roast books the catalogue's nominal, whatever is actually in the bag.
    const user = userEvent.setup();
    await renderPage();
    await user.click(screen.getByRole("radio", { name: /In — putting away/ }));
    await scan(user, "0700000000007");

    expect(await screen.findByText("This one weighs")).toBeInTheDocument();
    expect(screen.getByText(/Weight varies by package/)).toBeInTheDocument();
    // Prefilled with the nominal so accepting it is one tap and correcting it is
    // a few keystrokes — required, but never in the way.
    // The input takes the VISIBLE label as its accessible name (QuantityStepper).
    expect(screen.getByLabelText("This one weighs")).toHaveValue("4.87");
  });

  it("a catch-weight item checks in one package at a time", async () => {
    // The weight field describes THIS package, so a count above one would
    // record N roasts as all weighing the same — the exact fiction the
    // per-package model removes.
    const user = userEvent.setup();
    await renderPage();
    await user.click(screen.getByRole("radio", { name: /In — putting away/ }));
    await scan(user, "0700000000007");

    await screen.findByText("This one weighs");
    // The input takes the VISIBLE label as its accessible name (QuantityStepper).
    expect(screen.getByLabelText("Packages")).toHaveValue("1");
    expect(screen.getByLabelText("Packages")).toBeDisabled();

    await user.click(screen.getByRole("button", { name: /Add to inventory/ }));
    await waitFor(() =>
      expect(apiPost).toHaveBeenCalledWith(
        "/api/inventory/add",
        expect.objectContaining({ sealed_count: 1, container_size: 4.87, container_unit: "lb" }),
      ),
    );
  });
});
