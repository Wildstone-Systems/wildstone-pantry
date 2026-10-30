import { cleanup, fireEvent, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { renderWithProviders } from "@/test-utils";
import { ReviewedPurchaseCsvCard } from "./ReviewedPurchaseCsvCard";

const mocks = vi.hoisted(() => ({
  post: vi.fn(),
  status: vi.fn(),
  location: "loc-a" as string | null,
  admin: true,
  items: [] as { id: string; name: string; brand?: string | null; barcode?: string | null }[],
}));
vi.mock("@/api/client", () => ({ api: { post: mocks.post } }));
vi.mock("@/modules/catalog", () => ({
  useItems: () => ({ data: mocks.items }),
}));
vi.mock("@/state/AuthProvider", () => ({ useAuth: () => ({ isAdmin: mocks.admin, account: { id: "actor" } }) }));
vi.mock("@/state/AppStateProvider", async () => {
  const React = await import("react");
  return {
    AppStateProvider: ({ children }: { children: React.ReactNode }) => children,
    useAppState: () => ({
      selectedLocationId: React.useSyncExternalStore(
        (callback) => {
          window.addEventListener("csv-test-location", callback);
          return () => window.removeEventListener("csv-test-location", callback);
        },
        () => mocks.location
      ),
      setStatus: mocks.status,
    }),
  };
});
const first = {
  line_id: "row-1",
  raw_line: "Milk,2,each",
  name: "Milk",
  quantity: "2",
  unit: "each",
  barcode: null,
  matched_item_id: "milk",
  matched_item_name: "Milk",
  create_new: false,
  role: "stock",
  excluded: false,
  confirmed: false,
  source_quantity: "2",
  source_nonstock: false,
  resolution: "name_or_canonical",
  warnings: [],
};
const second = {
  ...first,
  line_id: "row-2",
  raw_line: "Bread,1,each",
  name: "Bread",
  quantity: "1",
  source_quantity: "1",
  matched_item_id: "bread",
};
let data: any;
beforeEach(() => {
  mocks.location = "loc-a";
  mocks.admin = true;
  mocks.items = [
    { id: "milk", name: "Milk" },
    { id: "bread", name: "Bread" },
  ];
  mocks.post.mockReset();
  mocks.status.mockReset();
  data = {
    draft_id: "draft",
    token: "opaque-token",
    fingerprint: "f".repeat(64),
    location_id: "loc-a",
    duplicate_of_purchase_id: null,
    lines: [structuredClone(first), structuredClone(second)],
  };
  mocks.post.mockImplementation(async (url: string) => ({
    data: url.endsWith("draft-csv")
      ? structuredClone(data)
      : url.endsWith("review")
        ? { annotations: [], provider_requests: 0 }
        : { purchase_id: "purchase", line_count: 1, status: "processed", lines: [] },
  }));
});
afterEach(cleanup);
async function preview() {
  const view = renderWithProviders(<ReviewedPurchaseCsvCard />);
  fireEvent.change(screen.getByLabelText("Purchase CSV text"), { target: { value: "original CSV" } });
  fireEvent.click(screen.getByRole("button", { name: "Preview CSV" }));
  await screen.findByLabelText("CSV item name 1");
  return { ...view, user: userEvent.setup() };
}

describe("Reviewed purchase CSV", () => {
  it("requires explicit verification and submits selected edited rows instead of raw CSV", async () => {
    const { user } = await preview();
    expect(screen.getByText("Classifier suggestion — human verification required")).toBeVisible();
    expect(screen.getByText(/A confidence score is not validated accuracy/)).toBeVisible();
    expect(screen.getByRole("button", { name: "Commit reviewed CSV" })).toBeDisabled();
    await user.click(screen.getByRole("textbox", { name: "CSV catalog item 1" }));
    await user.click(screen.getByRole("option", { name: "Bread" }));
    await user.clear(screen.getByLabelText("CSV item name 2"));
    await user.click(screen.getByRole("button", { name: "Exclude CSV row 2" }));
    await user.click(screen.getByLabelText("I verified CSV row 1"));
    await user.click(screen.getByRole("button", { name: "Commit reviewed CSV" }));
    await screen.findByText(/Committed 1 verified rows/);
    const payload = mocks.post.mock.calls.find(([url]) => String(url).endsWith("commit"))?.[1];
    expect(payload).not.toHaveProperty("csv_text");
    expect(payload.lines[0]).toMatchObject({ line_id: "row-1", matched_item_id: "bread", confirmed: true });
    expect(payload.lines[1]).toMatchObject({ line_id: "row-2", raw_line: "Bread,1,each", name: "", excluded: true });
    expect(mocks.post.mock.calls.some(([url]) => String(url).endsWith("import-csv"))).toBe(false);
  });
  it("resets verification and old hints after edits", async () => {
    const { user } = await preview();
    await user.click(screen.getByLabelText("I verified CSV row 1"));
    await user.clear(screen.getByLabelText("CSV quantity 1"));
    await user.type(screen.getByLabelText("CSV quantity 1"), "3");
    expect(screen.getByLabelText("I verified CSV row 1")).not.toBeChecked();
    expect(screen.getByRole("button", { name: "Commit reviewed CSV" })).toBeDisabled();
  });
  it("keeps excluded rows visible and restores them without implicit confirmation", async () => {
    const { user } = await preview();
    await user.click(screen.getByRole("button", { name: "Exclude CSV row 2" }));
    expect(screen.getByText("Excluded — will not import")).toBeVisible();
    await user.click(screen.getByRole("button", { name: "Restore CSV row 2" }));
    expect(screen.getByLabelText("I verified CSV row 2")).not.toBeChecked();
    expect(screen.getByText("Bread,1,each")).toBeVisible();
  });
  it("shows invalid and return source quantities rather than positive defaults", async () => {
    data.lines[1] = {
      ...data.lines[1],
      quantity: "-2",
      source_quantity: "-2",
      source_nonstock: true,
      role: "return",
      warnings: ["Return: exclude"],
    };
    const { user } = await preview();
    expect(screen.getByLabelText("CSV quantity 2")).toHaveValue("-2");
    expect(screen.getByLabelText("I verified CSV row 2")).toBeDisabled();
    await user.click(screen.getByRole("button", { name: "Exclude CSV row 2" }));
    await user.click(screen.getByLabelText("I verified CSV row 1"));
    expect(screen.getByRole("button", { name: "Commit reviewed CSV" })).toBeEnabled();
  });
  it("keeps low-score hints advisory without selecting or verifying an item", async () => {
    const { user } = await preview();
    mocks.post.mockResolvedValueOnce({
      data: {
        provider_requests: 1,
        annotations: [
          {
            line_id: "row-1",
            status: "ok",
            score: 0.6,
            below_threshold: true,
            suggested_item_id: "bread",
            candidates: [{ id: "bread", name: "Bread" }],
          },
        ],
      },
    });
    await user.click(screen.getByRole("button", { name: "Ask classifier (admin)" }));
    await screen.findByText(/60% model score — Below threshold/);
    expect(screen.getByRole("textbox", { name: "CSV catalog item 1" })).toHaveValue("Milk");
    expect(screen.getByLabelText("I verified CSV row 1")).not.toBeChecked();
    expect(screen.getByRole("button", { name: "Commit reviewed CSV" })).toBeDisabled();
  });
  it("preserves manual completion when classifier is unavailable", async () => {
    const { user } = await preview();
    mocks.post.mockRejectedValueOnce(new Error("unavailable"));
    await user.click(screen.getByRole("button", { name: "Ask classifier (admin)" }));
    await waitFor(() => expect(mocks.status).toHaveBeenCalledWith(expect.stringContaining("finish manually")));
    await user.click(screen.getByLabelText("I verified CSV row 1"));
    await user.click(screen.getByLabelText("I verified CSV row 2"));
    expect(screen.getByRole("button", { name: "Commit reviewed CSV" })).toBeEnabled();
  });
  it("does not expose inference to kiosk users", async () => {
    mocks.admin = false;
    await preview();
    expect(screen.getByRole("button", { name: "Ask classifier (admin)" })).toBeDisabled();
    expect(screen.getByRole("link", { name: "administrator sign-in" })).toBeVisible();
    expect(mocks.post).toHaveBeenCalledTimes(1);
  });
  it("invalidates review when CSV/options change", async () => {
    const { user } = await preview();
    await user.click(screen.getByLabelText("Add verified rows to inventory"));
    expect(screen.queryByRole("button", { name: "Commit reviewed CSV" })).not.toBeInTheDocument();
    expect(screen.getByLabelText("Purchase CSV text")).toHaveValue("original CSV");
  });
  it("blocks duplicate sources and keeps manual rows visible", async () => {
    data.duplicate_of_purchase_id = "previous";
    await preview();
    expect(screen.getByText(/source was already imported/)).toBeVisible();
    expect(screen.getByRole("button", { name: "Commit reviewed CSV" })).toBeDisabled();
  });
  it("retains a failed commit draft for an idempotent retry", async () => {
    const { user } = await preview();
    await user.click(screen.getByLabelText("I verified CSV row 1"));
    await user.click(screen.getByLabelText("I verified CSV row 2"));
    mocks.post.mockRejectedValueOnce(new Error("response lost"));
    await user.click(screen.getByRole("button", { name: "Commit reviewed CSV" }));
    await waitFor(() => expect(mocks.status).toHaveBeenCalledWith(expect.stringContaining("Keep this draft")));
    await user.click(screen.getByRole("button", { name: "Commit reviewed CSV" }));
    await screen.findByText(/Committed 1 verified rows/);
    const commits = mocks.post.mock.calls.filter(([url]) => String(url).endsWith("commit"));
    expect(commits[0][1]).toEqual(commits[1][1]);
  });

  it("distinguishes same-name brands before explicit selection and commit", async () => {
    mocks.items = [
      { id: "milk", name: "Milk", brand: "Dairy Farm" },
      { id: "oat", name: "Milk", brand: "Oat Farm" },
      { id: "bread", name: "Bread" },
    ];
    const { user } = await preview();
    await user.click(screen.getByRole("textbox", { name: "CSV catalog item 1" }));
    expect(screen.getByRole("option", { name: "Milk (Dairy Farm)" })).toBeVisible();
    await user.click(screen.getByRole("option", { name: "Milk (Oat Farm)" }));
    await user.click(screen.getByRole("button", { name: "Exclude CSV row 2" }));
    await user.click(screen.getByLabelText("I verified CSV row 1"));
    await user.click(screen.getByRole("button", { name: "Commit reviewed CSV" }));
    await screen.findByText(/Committed 1 verified rows/);
    expect(mocks.post.mock.calls.find(([url]) => String(url).endsWith("commit"))?.[1].lines[0].matched_item_id).toBe(
      "oat"
    );
  });
  it("names the proposed role and scored match while preserving other choices", async () => {
    const { user } = await preview();
    mocks.post.mockResolvedValueOnce({
      data: {
        provider_requests: 1,
        annotations: [
          {
            line_id: "row-1",
            status: "ok",
            score: 0.95,
            below_threshold: false,
            role: "stock",
            suggested_item_id: "bread",
            candidates: [
              { id: "milk", name: "Milk" },
              { id: "bread", name: "Bread" },
            ],
          },
        ],
      },
    });
    await user.click(screen.getByRole("button", { name: "Ask classifier (admin)" }));
    await screen.findByText("Suggested match: Bread");
    expect(screen.getByText("Suggested role: Stock purchase")).toBeVisible();
    expect(screen.getByText("Alternative candidate: Milk")).toBeVisible();
    expect(screen.getByRole("textbox", { name: "CSV catalog item 1" })).toHaveValue("Milk");
    expect(screen.getByLabelText("I verified CSV row 1")).not.toBeChecked();
    expect(screen.getByRole("button", { name: "Commit reviewed CSV" })).toBeDisabled();
  });
  it("shows a readable saved selection when catalog cache does not contain its ID", async () => {
    data.lines[0] = {
      ...data.lines[0],
      matched_item_id: "old",
      matched_item_name: "Saved product",
      matched_item_brand: "Saved brand",
    };
    await preview();
    expect(screen.getByRole("textbox", { name: "CSV catalog item 1" })).toHaveValue(
      "Saved product (Saved brand) — saved selection"
    );
  });
  it("does not present an unsupported identity as a positive recommendation", async () => {
    const { user } = await preview();
    mocks.post.mockResolvedValueOnce({
      data: {
        provider_requests: 1,
        annotations: [
          {
            line_id: "row-1",
            status: "ok",
            score: 0.99,
            below_threshold: false,
            role: "unknown",
            suggested_item_id: null,
            candidates: [],
          },
        ],
      },
    });
    await user.click(screen.getByRole("button", { name: "Ask classifier (admin)" }));
    await screen.findByText("No supported catalog match suggested; identify the item manually.");
    expect(screen.getByText("Suggested role: Unknown / insufficient evidence")).toBeVisible();
    expect(screen.getByLabelText("I verified CSV row 1")).not.toBeChecked();
  });
});
