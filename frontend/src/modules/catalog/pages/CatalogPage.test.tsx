import { cleanup, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { renderWithProviders } from "@/test-utils";

const item = {
  id: "item-1",
  name: "Olive Oil",
  canonical_ingredient: "olive oil",
  canonical_provenance: "confirmed",
  canonical_version: 2,
  canonical_revision: 3,
  brand: "Bertolli",
  barcode: "0123456789012",
  default_expiration_days: null,
  restock_threshold: 2,
  calories_per_unit: null,
  energy_kcal_100g: 884,
  protein_g_100g: 0,
  fat_g_100g: 100,
  carbs_g_100g: 0,
  serving_size_g: null,
  density_g_per_ml: 0.91,
  grams_per_each: null,
  nutrition: { "energy-kcal_100g": 884, fat_100g: 100 },
  is_active: true,
  created_at: "2026-02-14T00:00:00Z",
};

const location = {
  id: "loc-1",
  name: "Pantry",
  description: "dry goods",
  is_active: true,
  created_at: "2026-02-14T00:00:00Z",
};

const apiGet = vi.fn(async (url: string, config?: { params?: { include_inactive?: boolean } }) => {
  const includeInactive = config?.params?.include_inactive ?? false;
  if (url === "/api/items") {
    return {
      data: {
        items: includeInactive ? [item, { ...item, id: "item-2", name: "Old Cereal", is_active: false }] : [item],
      },
    };
  }
  if (url === "/api/locations") return { data: { locations: [location] } };
  return { data: {} };
});
const apiPut = vi.fn(async (url: string, body: Record<string, unknown>) => ({ data: { ...item, ...body } }));

vi.mock("@/api/client", () => ({
  api: { get: apiGet, put: apiPut },
}));

describe("CatalogPage archive / edit", () => {
  beforeEach(() => {
    apiPut.mockClear();
    apiGet.mockClear();
  });

  // Vitest runs here without `globals: true`, so RTL's automatic afterEach
  // cleanup isn't registered — unmount between renders ourselves.
  afterEach(() => cleanup());

  it("requires explicit global confirmation before editing identity and sends stale-write guards", async () => {
    const user = userEvent.setup();
    const { CatalogPage } = await import("./CatalogPage");
    renderWithProviders(<CatalogPage />, { initialEntries: ["/catalog"] });
    await user.click(await screen.findByRole("button", { name: "Edit Olive Oil" }));
    const identity = await screen.findByLabelText("Ingredient identity key");
    await user.clear(identity);
    await user.paste("extra virgin olive oil");
    expect(screen.getByRole("button", { name: "Save changes" })).toBeDisabled();
    expect(apiPut).not.toHaveBeenCalled();
    await user.click(screen.getByLabelText("I confirm this product's ingredient identity for all recipes"));
    await user.click(screen.getByRole("button", { name: "Save changes" }));
    await waitFor(() =>
      expect(apiPut).toHaveBeenCalledWith(
        "/api/items/item-1",
        expect.objectContaining({
          canonical_ingredient: "extra virgin olive oil",
          confirm_identity_change: true,
          expected_canonical_ingredient: "olive oil",
          expected_canonical_revision: 3,
        })
      )
    );
  });

  it("edits an item through the modal and PUTs the changes", async () => {
    const user = userEvent.setup();
    const { CatalogPage } = await import("./CatalogPage");
    renderWithProviders(<CatalogPage />, { initialEntries: ["/catalog"] });

    await user.click(await screen.findByRole("button", { name: "Edit Olive Oil" }));

    const nameInput = await screen.findByLabelText(/Item name/);
    await user.clear(nameInput);
    await user.type(nameInput, "Olive Oil (large)");

    await user.click(screen.getByRole("button", { name: "Save changes" }));

    await waitFor(() => expect(apiPut).toHaveBeenCalledTimes(1));
    expect(apiPut).toHaveBeenCalledWith(
      "/api/items/item-1",
      expect.objectContaining({ name: "Olive Oil (large)", brand: "Bertolli", barcode: "0123456789012" })
    );
    // Editing must not touch archived status.
    expect(apiPut.mock.calls[0][1]).not.toHaveProperty("is_active");
  });

  it("shows the item's nutrition in the edit modal and PUTs edited macros", async () => {
    const user = userEvent.setup();
    const { CatalogPage } = await import("./CatalogPage");
    renderWithProviders(<CatalogPage />, { initialEntries: ["/catalog"] });

    await user.click(await screen.findByRole("button", { name: "Edit Olive Oil" }));

    // Existing OFF-sourced nutrition is surfaced (fat 100 g/100g seeded into the form).
    const fatInput = (await screen.findByLabelText(/^Fat/)) as HTMLInputElement;
    expect(fatInput.value).toContain("100");

    // Edit a macro and save — it must be included in the PUT body.
    const proteinInput = await screen.findByLabelText(/^Protein/);
    await user.clear(proteinInput);
    await user.type(proteinInput, "5");

    await user.click(screen.getByRole("button", { name: "Save changes" }));

    await waitFor(() => expect(apiPut).toHaveBeenCalledTimes(1));
    expect(apiPut).toHaveBeenCalledWith(
      "/api/items/item-1",
      expect.objectContaining({ protein_g_100g: 5, fat_g_100g: 100, energy_kcal_100g: 884 })
    );
  });

  it("archives an active item via is_active=false", async () => {
    const user = userEvent.setup();
    const { CatalogPage } = await import("./CatalogPage");
    renderWithProviders(<CatalogPage />, { initialEntries: ["/catalog"] });

    await user.click(await screen.findByRole("button", { name: "Archive Olive Oil" }));

    await waitFor(() => expect(apiPut).toHaveBeenCalledWith("/api/items/item-1", { is_active: false }));
  });

  it("archives a location via is_active=false", async () => {
    const user = userEvent.setup();
    const { CatalogPage } = await import("./CatalogPage");
    renderWithProviders(<CatalogPage />, { initialEntries: ["/catalog"] });

    await user.click(await screen.findByRole("button", { name: "Archive Pantry" }));

    await waitFor(() => expect(apiPut).toHaveBeenCalledWith("/api/locations/loc-1", { is_active: false }));
  });

  it("reveals archived items and offers reactivation when 'Show archived' is on", async () => {
    const user = userEvent.setup();
    const { CatalogPage } = await import("./CatalogPage");
    renderWithProviders(<CatalogPage />, { initialEntries: ["/catalog"] });

    // Toggle the items section's archived switch (first one on the page).
    const switches = await screen.findAllByRole("switch", { name: "Show archived" });
    await user.click(switches[0]);

    const reactivate = await screen.findByRole("button", { name: "Reactivate Old Cereal" });
    await user.click(reactivate);

    await waitFor(() => expect(apiPut).toHaveBeenCalledWith("/api/items/item-2", { is_active: true }));
  });
});
