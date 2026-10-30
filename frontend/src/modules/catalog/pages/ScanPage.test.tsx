import { cleanup, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { renderWithProviders } from "@/test-utils";

const apiGet = vi.fn();
const apiPost = vi.fn();

vi.mock("@/api/client", () => ({
  api: { get: (...a: unknown[]) => apiGet(...a), post: (...a: unknown[]) => apiPost(...a) },
}));

// zxing pulls in camera APIs jsdom doesn't have; the tests here are keyboard-only.
vi.mock("@zxing/browser", () => ({
  BrowserMultiFormatReader: class {
    decodeFromVideoDevice() {
      return Promise.resolve({ stop: () => {} });
    }
  },
}));

const LOCATIONS = [
  { id: "pantry", name: "Kitchen Pantry", is_active: true },
  { id: "fridge", name: "Fridge", is_active: true },
  { id: "garage", name: "Garage Shelf", is_active: true },
];

/** A brand-new item: the backend offers EVERY location, all with zero on hand. */
const NEW_ITEM_SCAN = {
  barcode: "111",
  found: true,
  created: true,
  item_id: "i1",
  item_name: "Ground Cumin",
  source: "external:off",
  item: {},
  product: null,
  location_candidates: LOCATIONS.map((l) => ({ location_id: l.id, location_name: l.name, quantity: "0" })),
  shelf_life_hint: null,
};

/** An item genuinely stocked in two places — ambiguous, must be asked. */
const AMBIGUOUS_SCAN = {
  ...NEW_ITEM_SCAN,
  created: false,
  item_name: "Butter",
  location_candidates: [
    { location_id: "fridge", location_name: "Fridge", quantity: "2" },
    { location_id: "garage", location_name: "Garage Shelf", quantity: "1" },
  ],
};

function mockScan(response: unknown) {
  apiPost.mockImplementation(async (url: string) => {
    if (url === "/api/barcodes/scan") return { data: response };
    return { data: { transaction_ids: ["t1"], inventory: [], added_quantity: null, added_unit: null } };
  });
}

describe("ScanPage best-by estimation", () => {
  beforeEach(() => {
    apiGet.mockReset();
    apiPost.mockReset();
    window.localStorage.clear();
    window.localStorage.setItem("pantry_selected_location_id", "pantry");
    apiGet.mockImplementation(async (url: string) => {
      if (url === "/api/locations") return { data: { locations: LOCATIONS } };
      return { data: {} };
    });
  });
  afterEach(() => {
    cleanup();
    window.localStorage.clear();
  });

  it("leaves best-by blank when the backend has nothing to say", async () => {
    const user = userEvent.setup();
    mockScan(NEW_ITEM_SCAN); // shelf_life_hint: null
    const { ScanPage } = await import("./ScanPage");
    const { container } = renderWithProviders(<ScanPage />, { initialEntries: ["/scan"] });

    await user.type(await screen.findByPlaceholderText(/Scan or type a UPC/i), "111{enter}");
    await screen.findByText(/going to/i);

    // Silence is the default: no date, and nothing claiming to have derived one.
    expect(screen.queryByText(/estimated/i)).toBeNull();
    expect((container.querySelector('input[type="date"]') as HTMLInputElement).value).toBe("");
  });

  it("marks a learned date as an estimate and explains where it came from", async () => {
    const user = userEvent.setup();
    mockScan({
      ...NEW_ITEM_SCAN,
      shelf_life_hint: { days: 14, observations: 3, spread_days: 2, source: "learned" },
    });
    const { ScanPage } = await import("./ScanPage");
    const { container } = renderWithProviders(<ScanPage />, { initialEntries: ["/scan"] });

    await user.type(await screen.findByPlaceholderText(/Scan or type a UPC/i), "111{enter}");
    await screen.findByText(/going to/i);

    // Prefilled 14 days out...
    const expected = new Date();
    expected.setDate(expected.getDate() + 14);
    expect((container.querySelector('input[type="date"]') as HTMLInputElement).value).toBe(
      expected.toISOString().slice(0, 10),
    );

    // ...and visibly flagged as a guess, with the reasoning one hover away.
    const badge = screen.getByLabelText("How this best-by date was estimated");
    expect(badge).toHaveTextContent(/estimated/i);
    await user.hover(badge);
    const tip = await screen.findByText(/last 3 best-by dates/i);
    expect(tip).toHaveTextContent(/14 days from check-in/i);
    expect(tip).toHaveTextContent(/varied by 2 days/i);
    expect(tip).toHaveTextContent(/it's a guess/i);
  });

  it("stops calling the date an estimate once the operator edits it", async () => {
    const user = userEvent.setup();
    mockScan({
      ...NEW_ITEM_SCAN,
      shelf_life_hint: { days: 14, observations: 3, spread_days: 2, source: "learned" },
    });
    const { ScanPage } = await import("./ScanPage");
    renderWithProviders(<ScanPage />, { initialEntries: ["/scan"] });

    await user.type(await screen.findByPlaceholderText(/Scan or type a UPC/i), "111{enter}");
    await screen.findByLabelText("How this best-by date was estimated");

    // Reading the real date off the package makes it theirs, not ours.
    await user.click(screen.getByRole("button", { name: "+1wk" }));
    expect(screen.queryByLabelText("How this best-by date was estimated")).toBeNull();
  });
});

describe("ScanPage location defaulting", () => {
  beforeEach(() => {
    apiGet.mockReset();
    apiPost.mockReset();
    window.localStorage.clear();
    // The location set at the top of the page.
    window.localStorage.setItem("pantry_selected_location_id", "pantry");
    apiGet.mockImplementation(async (url: string) => {
      if (url === "/api/locations") return { data: { locations: LOCATIONS } };
      return { data: {} };
    });
  });
  afterEach(() => {
    cleanup();
    window.localStorage.clear();
  });

  it("a brand-new item defaults to the page's location so scanning doesn't need a tap each time", async () => {
    const user = userEvent.setup();
    mockScan(NEW_ITEM_SCAN);
    const { ScanPage } = await import("./ScanPage");
    renderWithProviders(<ScanPage />, { initialEntries: ["/scan"] });

    await user.type(await screen.findByPlaceholderText(/Scan or type a UPC/i), "111{enter}");

    // Still shows the picker (you can override) but already has a destination.
    await screen.findByText(/going to/i);
    // The default is offered as a (highlighted) button in the picker.
    expect(screen.getByRole("button", { name: /Kitchen Pantry/ })).toBeInTheDocument();

    // ...and Add works immediately, with no location tap.
    await user.click(await screen.findByRole("button", { name: /Add & next/i }));
    await waitFor(() =>
      expect(apiPost).toHaveBeenCalledWith(
        "/api/inventory/add",
        expect.objectContaining({ item_id: "i1", location_id: "pantry" }),
      ),
    );
  });

  it("keeps the scanner strip thin while the camera is off", async () => {
    const user = userEvent.setup();
    mockScan(NEW_ITEM_SCAN);
    const { ScanPage } = await import("./ScanPage");
    const { container } = renderWithProviders(<ScanPage />, { initialEntries: ["/scan"] });

    // The preview box is what makes the top card tall; with the camera off it
    // must take zero height, or the item card below is pushed off-screen.
    const preview = container.querySelector("video")?.parentElement as HTMLElement;
    expect(preview.style.height).toBe("0px");

    // The keyboard-wedge hint is one-time chrome: it goes away once a result
    // is on screen so it stops competing for vertical space mid-scan.
    expect(screen.getByText(/acts as a keyboard/i)).toBeInTheDocument();
    await user.type(await screen.findByPlaceholderText(/Scan or type a UPC/i), "111{enter}");
    await screen.findByText(/going to/i);
    expect(screen.queryByText(/acts as a keyboard/i)).toBeNull();
  });

  it("an item stocked in several places is still asked about — no silent guess", async () => {
    const user = userEvent.setup();
    mockScan(AMBIGUOUS_SCAN);
    const { ScanPage } = await import("./ScanPage");
    renderWithProviders(<ScanPage />, { initialEntries: ["/scan"] });

    await user.type(await screen.findByPlaceholderText(/Scan or type a UPC/i), "111{enter}");

    // Real stock in two places: guessing could decrement the wrong shelf.
    await screen.findByText(/pick where to add it/i);
    expect(screen.queryByText(/going to/i)).toBeNull();

    await user.click(screen.getByRole("button", { name: /Fridge/ }));
    await user.click(await screen.findByRole("button", { name: /Add & next/i }));
    await waitFor(() =>
      expect(apiPost).toHaveBeenCalledWith(
        "/api/inventory/add",
        expect.objectContaining({ location_id: "fridge" }),
      ),
    );
  });
});
