import { cleanup, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { renderWithProviders } from "@/test-utils";
import { EditItemModal } from "./EditItemModal";
import { UsdaSearchModal } from "./UsdaSearchModal";
import type { Item } from "../hooks/catalog-hooks";

const apiGet = vi.fn();
const apiPost = vi.fn();
vi.mock("@/api/client", () => ({
  api: { get: (...a: unknown[]) => apiGet(...a), post: (...a: unknown[]) => apiPost(...a) },
}));
const item = {
  id: "i1",
  name: "Banana",
  brand: null,
  barcode: null,
  nutrition_estimated: true,
  nutrition_source: "Bananas raw",
  nutrition: {},
  energy_kcal_100g: 89,
} as Item;
const candidate = {
  fdc_id: 42,
  description: "Bananas raw",
  source: "usda_fdc",
  verification_status: "unverified",
  data_type: "Foundation",
  calories: 89,
  protein_g: 1,
  fat_g: 0,
  carbs_g: 23,
};

describe("nutrition reference review", () => {
  beforeEach(() => {
    apiGet.mockReset();
    apiPost.mockReset();
    apiGet.mockResolvedValue({ data: { candidates: [candidate], throttled: false } });
  });
  afterEach(cleanup);

  it("keeps historical reference approval unresolved when provenance is absent", async () => {
    renderWithProviders(<EditItemModal item={item} onClose={vi.fn()} />);
    expect(await screen.findByText(/Confirmation unknown; source: Unknown source/)).toBeInTheDocument();
    expect(screen.getByText(/Reference match remains unresolved/)).toBeInTheDocument();
    expect(apiPost).not.toHaveBeenCalled();
  });

  it("shows persisted human selection with source and FDC evidence", async () => {
    renderWithProviders(
      <EditItemModal
        item={{
          ...item,
          nutrition_reference: {
            status: "human_selected",
            source: "usda_fdc",
            fdc_id: 42,
            description: "Bananas raw",
            why: "Explicit user choice",
          },
        }}
        onClose={vi.fn()}
      />
    );
    expect(await screen.findByText(/Chosen by you; source: USDA FoodData Central/)).toBeInTheDocument();
    expect(screen.getByText("FDC ID: 42")).toBeInTheDocument();
    expect(screen.queryByText(/Reference match remains unresolved/)).not.toBeInTheDocument();
  });

  it("shows human-entered nutrition with manual authority", async () => {
    renderWithProviders(
      <EditItemModal
        item={{
          ...item,
          nutrition_estimated: false,
          nutrition_source: null,
          nutrition_reference: {
            status: "manual",
            source: "manual",
            fdc_id: null,
            description: null,
            why: "Human-entered nutrition",
          },
        }}
        onClose={vi.fn()}
      />
    );
    expect(await screen.findByText(/Entered manually; source: Your nutrition entry/)).toBeInTheDocument();
    expect(screen.getByText("Human-entered nutrition")).toBeInTheDocument();
    expect(screen.queryByText(/Reference match remains unresolved/)).not.toBeInTheDocument();
    expect(screen.queryByText(/Historical nutrition has no recorded/)).not.toBeInTheDocument();
  });

  it.each(["rejected", "unverified", "unavailable"])(
    "surfaces %s rescan outcome instead of a silent no-change",
    async (status) => {
      apiPost.mockResolvedValue({
        data: {
          item,
          item_id: "i1",
          status: "no_change",
          filled: [],
          estimated: false,
          estimated_source: null,
          reference_review: {
            status,
            source: "usda_fdc",
            description: "Bananas raw",
            fdc_id: 42,
            why: "Match needs confirmation",
          },
        },
      });
      const user = userEvent.setup();
      renderWithProviders(<EditItemModal item={item} onClose={vi.fn()} />);
      await user.click(await screen.findByRole("button", { name: "Auto-fill from USDA" }));
      expect(
        await screen.findByText(
          `${({ rejected: "Match rejected", unverified: "Needs review", unavailable: "Verification unavailable" } as Record<string, string>)[status]}; source: USDA FoodData Central.`
        )
      ).toBeInTheDocument();
      expect(screen.getByText(/No automatic nutrition was applied/)).toBeInTheDocument();
    }
  );

  it("searching candidates does not persist or authorize them", async () => {
    const pick = vi.fn();
    const user = userEvent.setup();
    renderWithProviders(<UsdaSearchModal seedQuery="banana" opened onClose={vi.fn()} onPick={pick} />);
    expect(await screen.findByText(/Source: USDA FoodData Central/)).toHaveTextContent("Unverified");
    expect(screen.getByText(/Search results are unverified candidates/)).toBeInTheDocument();
    expect(apiPost).not.toHaveBeenCalled();
    expect(pick).not.toHaveBeenCalled();
    await user.click(screen.getByRole("button", { name: "Use this" }));
    expect(pick).toHaveBeenCalledWith(candidate, "banana");
    expect(apiPost).not.toHaveBeenCalled();
  });

  it("only an explicit picker choice applies a reference and refreshes provenance", async () => {
    const user = userEvent.setup();
    apiPost.mockResolvedValue({
      data: {
        ...item,
        nutrition_reference: {
          status: "human_selected",
          source: "usda_fdc",
          fdc_id: 42,
          description: "Bananas raw",
          why: "Explicit user choice",
        },
      },
    });
    renderWithProviders(<EditItemModal item={item} onClose={vi.fn()} />);
    await user.click(await screen.findByRole("button", { name: /Match to a USDA food/ }));
    expect(await screen.findByText(/Source: USDA FoodData Central/)).toBeInTheDocument();
    expect(apiPost).not.toHaveBeenCalled();
    await user.click(screen.getByRole("button", { name: "Use this" }));
    await waitFor(() =>
      expect(apiPost).toHaveBeenCalledWith("/api/items/i1/nutrition-from-usda", { fdc_id: 42, q: "Banana" })
    );
    expect(await screen.findByText(/Chosen by you; source: USDA FoodData Central/)).toBeInTheDocument();
  });
});
