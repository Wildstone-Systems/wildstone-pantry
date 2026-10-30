import { cleanup, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { renderWithProviders } from "@/test-utils";

const apiGet = vi.fn();
const apiPost = vi.fn();

vi.mock("@/api/client", () => ({
  api: { get: (...a: unknown[]) => apiGet(...a), post: (...a: unknown[]) => apiPost(...a) },
}));

import { ConversionReviewPage } from "./ConversionReviewPage";

const PEPPER = {
  item_id: "i1",
  name: "Black pepper grinder refill",
  brand: null,
  base_unit: "each",
  stock_mode: "counted",
  size_quantity: 3,
  size_unit: "oz",
  recipe_units: ["tsp"],
};
const GARLIC = { ...PEPPER, item_id: "i2", name: "Minced Garlic", base_unit: "g", recipe_units: ["tsp"] };

const VERIFIED = {
  item_id: "i1",
  density_g_per_ml: 0.4666,
  source_description: "Spices, pepper, black",
  fdc_id: 170931,
  verified: true,
  verified_why: "black pepper refill is black pepper",
  grams_per_tsp: 2.3,
  grams_per_tbsp: 6.9,
  grams_per_cup: 110.4,
};

function mockQueue(items: unknown[], judge = true, suggestion: unknown = VERIFIED) {
  apiGet.mockImplementation(async (url: string) => {
    if (url === "/api/items/conversion-review") return { data: { items, judge_available: judge } };
    if (url.includes("/density-suggestion")) return { data: suggestion };
    return { data: {} };
  });
}

describe("ConversionReviewPage", () => {
  beforeEach(() => {
    apiGet.mockReset();
    apiPost.mockReset();
    apiPost.mockResolvedValue({ data: {} });
  });
  afterEach(cleanup);

  it("does not fetch any suggestion until asked", async () => {
    mockQueue([PEPPER, GARLIC]);
    renderWithProviders(<ConversionReviewPage />);
    await screen.findByText("Black pepper grinder refill");

    // The queue is one cheap query; lookups are 2.5-11s each against a
    // rate-limited API, so opening the page must not fire 21 of them.
    expect(apiGet.mock.calls.filter(([u]) => String(u).includes("density-suggestion"))).toHaveLength(0);
    expect(screen.getAllByRole("button", { name: /find conversion/i })).toHaveLength(2);
  });

  it("shows the weight of a spoonful, its source, and that it was checked", async () => {
    const user = userEvent.setup();
    mockQueue([PEPPER]);
    renderWithProviders(<ConversionReviewPage />);

    await user.click(await screen.findByRole("button", { name: /find conversion/i }));

    // A person can check "1 tsp = 2.30 g". Nobody can check "0.4666 g/ml".
    expect(await screen.findByText("1 tsp = 2.30 g")).toBeInTheDocument();
    expect(screen.getByText(/Spices, pepper, black/)).toBeInTheDocument();
    expect(screen.getByText(/checked — same food as yours/)).toBeInTheDocument();
  });

  it("saves the density, not the grams, when the suggestion is accepted", async () => {
    const user = userEvent.setup();
    mockQueue([PEPPER]);
    renderWithProviders(<ConversionReviewPage />);
    await user.click(await screen.findByRole("button", { name: /find conversion/i }));
    await user.click(await screen.findByRole("button", { name: /use this/i }));

    await waitFor(() => expect(apiPost).toHaveBeenCalledWith("/api/items/i1/density", { density_g_per_ml: 0.4666 }));
  });

  it("says plainly when nothing vetted the match", async () => {
    const user = userEvent.setup();
    mockQueue([PEPPER], false, { ...VERIFIED, verified: null, verified_why: "" });
    renderWithProviders(<ConversionReviewPage />);

    expect(await screen.findByText(/No local model configured/)).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: /find conversion/i }));
    expect(await screen.findByText(/not checked — confirm this is the same food/)).toBeInTheDocument();
  });

  it("shows a rejected density reference distinctly and never saves during lookup", async () => {
    const user = userEvent.setup();
    mockQueue([PEPPER], true, { ...VERIFIED, verified: false, verified_why: "Different food" });
    renderWithProviders(<ConversionReviewPage />);
    await user.click(await screen.findByRole("button", { name: /find conversion/i }));
    expect(await screen.findByText(/rejected — Different food/)).toBeInTheDocument();
    expect(screen.getByText(/USDA, FDC ID: 170931/)).toBeInTheDocument();
    expect(apiPost).not.toHaveBeenCalled();
  });

  it("a refusal is not a dead end — manual entry converts from the recipe's unit", async () => {
    const user = userEvent.setup();
    mockQueue([GARLIC], true, null); // the gate found nothing it trusted
    renderWithProviders(<ConversionReviewPage />);
    await user.click(await screen.findByRole("button", { name: /find conversion/i }));

    await screen.findByText(/No confident match/);
    // Prompted in tsp — the unit the recipe uses — because a teaspoon can be weighed.
    const field = screen.getByLabelText(/1 tsp of Minced Garlic weighs/);
    await user.type(field, "2.8");
    await user.click(screen.getByRole("button", { name: /Save conversion for Minced Garlic/ }));

    // 2.8 g per tsp / 4.92892 ml = 0.568 g/ml
    await waitFor(() => {
      const [, body] = apiPost.mock.calls[0];
      expect((body as { density_g_per_ml: number }).density_g_per_ml).toBeCloseTo(0.568, 3);
    });
  });

  it("lets you re-search the food database with your own term", async () => {
    const user = userEvent.setup();
    mockQueue([GARLIC], true, null);
    renderWithProviders(<ConversionReviewPage />);
    await user.click(await screen.findByRole("button", { name: /find conversion/i }));
    await screen.findByText(/No confident match/);

    // Regression: this crashed the page in production. The handler read
    // `e.currentTarget.value` inside the setState updater, and React nulls
    // `currentTarget` once the handler returns — so the second keystroke threw
    // "can't access property value, e.currentTarget is null" and unmounted
    // everything. Typing MORE THAN ONE character is the whole test; one
    // character passed and hid it.
    await user.type(document.querySelector('input[placeholder="Minced Garlic"]') as HTMLInputElement, "garlic");

    const search = Array.from(document.querySelectorAll("button")).find(
      (b) => b.textContent?.trim() === "Search"
    ) as HTMLButtonElement;
    expect(search).toBeTruthy();
    expect(search.disabled).toBe(false);
    await user.click(search);

    await waitFor(() =>
      expect(apiGet).toHaveBeenCalledWith("/api/items/i2/density-suggestion", {
        params: { q: "garlic" },
      })
    );
  });

  it("find-all walks the queue one row at a time, never in parallel", async () => {
    const user = userEvent.setup();
    let inFlight = 0;
    let peak = 0;
    apiGet.mockImplementation(async (url: string) => {
      if (url === "/api/items/conversion-review") return { data: { items: [PEPPER, GARLIC], judge_available: true } };
      if (String(url).includes("density-suggestion")) {
        inFlight += 1;
        peak = Math.max(peak, inFlight);
        await new Promise((r) => setTimeout(r, 10));
        inFlight -= 1;
        return { data: VERIFIED };
      }
      return { data: {} };
    });
    renderWithProviders(<ConversionReviewPage />);

    await user.click(await screen.findByRole("button", { name: /find all conversions/i }));
    await waitFor(
      () => expect(apiGet.mock.calls.filter(([u]) => String(u).includes("density-suggestion"))).toHaveLength(2),
      { timeout: 5000 }
    );
    // The whole point of the rate limit: a fan-out would 400 the food database.
    expect(peak).toBe(1);
  });
});
