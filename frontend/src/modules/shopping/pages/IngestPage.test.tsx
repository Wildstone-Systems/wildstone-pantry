import type { ReceiptPreviewLine } from "@/api/hooks";
import { cleanup, fireEvent, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { renderWithProviders } from "@/test-utils";
import { IngestPage } from "./IngestPage";

const mocks = vi.hoisted(() => ({ post: vi.fn(), status: vi.fn(), location: "loc-a" as string | null }));
vi.mock("@/api/client", () => ({ api: { post: mocks.post } }));
vi.mock("@/api/hooks", () => ({
  useLocations: () => ({
    data: [
      { id: "loc-a", name: "Kitchen" },
      { id: "loc-b", name: "Garage" },
    ],
  }),
}));
vi.mock("@/state/AppStateProvider", async () => {
  const React = await import("react");
  return {
    AppStateProvider: ({ children }: { children: React.ReactNode }) => children,
    useAppState: () => ({
      selectedLocationId: React.useSyncExternalStore(
        (callback) => {
          window.addEventListener("test-location", callback);
          return () => window.removeEventListener("test-location", callback);
        },
        () => mocks.location
      ),
      setStatus: mocks.status,
      setSelectedLocationId: (value: string | null) => {
        mocks.location = value;
        window.dispatchEvent(new Event("test-location"));
      },
    }),
  };
});
vi.mock("@/modules/catalog", () => ({
  useItems: () => ({
    data: [
      { id: "milk", name: "Milk", brand: null },
      { id: "oat", name: "Oat milk", brand: null },
    ],
  }),
}));
const matched = {
  line_id: "line-1",
  role: "stock",
  source: "rules",
  excluded: false,
  confirmed: false,
  create_new: false,
  raw_line: "milk 2 each",
  name: "Milk",
  quantity: "2",
  unit: "each",
  matched_item_id: "milk",
  matched_item_name: "Milk",
  expiration_days_estimate: null,
};
const unmatched = {
  ...matched,
  line_id: "line-2",
  name: "Bread",
  raw_line: "bread 1 each",
  matched_item_id: null,
  matched_item_name: null,
};
async function preview(lines: ReceiptPreviewLine[] = [matched]) {
  mocks.post.mockResolvedValueOnce({
    data: { lines, line_count: lines.length, draft_id: "draft-1", token: "draft-token" },
  });
  const view = renderWithProviders(<IngestPage />);
  fireEvent.change(screen.getByLabelText("Receipt text"), { target: { value: "original receipt" } });
  fireEvent.click(screen.getByRole("button", { name: "Preview" }));
  await screen.findByLabelText("Item name 1");
  return view;
}
afterEach(cleanup);
beforeEach(() => {
  vi.clearAllMocks();
  mocks.location = "loc-a";
});
describe("Receipt review", () => {
  it("previews without writing and preserves the matched ID through name and quantity edits", async () => {
    await preview();
    expect(mocks.post).toHaveBeenCalledTimes(1);
    fireEvent.change(screen.getByLabelText("Item name 1"), { target: { value: "Renamed milk" } });
    fireEvent.change(screen.getByLabelText("Quantity 1"), { target: { value: "3" } });
    mocks.post.mockResolvedValueOnce({ data: { line_count: 1 } });
    screen.getAllByRole("checkbox").forEach((checkbox) => {
      if (!(checkbox as HTMLInputElement).disabled && !(checkbox as HTMLInputElement).checked)
        fireEvent.click(checkbox);
    });
    fireEvent.click(screen.getByRole("button", { name: "Commit to Inventory" }));
    await waitFor(() =>
      expect(mocks.post).toHaveBeenCalledWith(
        "/api/receipts/drafts/draft-1/commit",
        expect.objectContaining({
          location_id: "loc-a",
          raw_text: "original receipt",
          lines: [
            expect.objectContaining({
              matched_item_id: "milk",
              create_new: false,
              name: "Renamed milk",
              quantity: "3",
            }),
          ],
        })
      )
    );
  });
  it("allows choosing a different catalog ID", async () => {
    await preview();
    await userEvent.click(screen.getByLabelText("Catalog item 1", { selector: "input" }));
    await userEvent.click(screen.getByRole("option", { name: "Oat milk" }));
    mocks.post.mockResolvedValueOnce({ data: { line_count: 1 } });
    screen.getAllByRole("checkbox").forEach((checkbox) => {
      if (!(checkbox as HTMLInputElement).disabled && !(checkbox as HTMLInputElement).checked)
        fireEvent.click(checkbox);
    });
    fireEvent.click(screen.getByRole("button", { name: "Commit to Inventory" }));
    await waitFor(() =>
      expect(mocks.post).toHaveBeenLastCalledWith(
        "/api/receipts/drafts/draft-1/commit",
        expect.objectContaining({ lines: [expect.objectContaining({ matched_item_id: "oat", create_new: false })] })
      )
    );
  });
  it("requires an explicit Create new choice for unmatched lines", async () => {
    await preview([unmatched]);
    expect(screen.getByRole("button", { name: "Commit to Inventory" })).toBeDisabled();
    await userEvent.click(screen.getByLabelText("Catalog item 1", { selector: "input" }));
    await userEvent.click(screen.getByRole("option", { name: "Create new item" }));
    mocks.post.mockResolvedValueOnce({ data: { line_count: 1 } });
    screen.getAllByRole("checkbox").forEach((checkbox) => {
      if (!(checkbox as HTMLInputElement).disabled && !(checkbox as HTMLInputElement).checked)
        fireEvent.click(checkbox);
    });
    fireEvent.click(screen.getByRole("button", { name: "Commit to Inventory" }));
    await waitFor(() =>
      expect(mocks.post).toHaveBeenLastCalledWith(
        "/api/receipts/drafts/draft-1/commit",
        expect.objectContaining({ lines: [expect.objectContaining({ matched_item_id: null, create_new: true })] })
      )
    );
  });
  it("excludes lines and cancels without writing inventory", async () => {
    await preview([matched, unmatched]);
    fireEvent.click(screen.getByRole("button", { name: "Exclude line 2" }));
    expect(screen.getByLabelText("Item name 2")).toBeDisabled();
    expect(screen.getByText("Excluded — will not add stock")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Cancel review" }));
    expect(screen.queryByLabelText("Item name 1")).not.toBeInTheDocument();
    expect(mocks.post).toHaveBeenCalledTimes(1);
  });
  it("retains edited drafts after commit errors and rejects zero quantities", async () => {
    await preview();
    fireEvent.change(screen.getByLabelText("Quantity 1"), { target: { value: "0" } });
    expect(screen.getByRole("button", { name: "Commit to Inventory" })).toBeDisabled();
    fireEvent.change(screen.getByLabelText("Quantity 1"), { target: { value: "4" } });
    mocks.post.mockRejectedValueOnce(new Error("network failed"));
    screen.getAllByRole("checkbox").forEach((checkbox) => {
      if (!(checkbox as HTMLInputElement).disabled && !(checkbox as HTMLInputElement).checked)
        fireEvent.click(checkbox);
    });
    fireEvent.click(screen.getByRole("button", { name: "Commit to Inventory" }));
    await waitFor(() => expect(mocks.status).toHaveBeenCalledWith("Commit failed"));
    expect(screen.getByLabelText("Quantity 1")).toHaveValue("4");
    expect(screen.getByLabelText("Catalog item 1", { selector: "input" })).toHaveValue("Milk");
  });
  it("uploads photos to preview only and preserves source filename on explicit commit", async () => {
    mocks.post.mockResolvedValueOnce({
      data: { lines: [matched], line_count: 1, draft_id: "draft-1", token: "draft-token" },
    });
    renderWithProviders(<IngestPage />);
    const file = new File(["image"], "receipt.jpg", { type: "image/jpeg" });
    fireEvent.change(screen.getByLabelText("Receipt image"), { target: { files: [file] } });
    await screen.findByLabelText("Item name 1");
    expect(mocks.post).toHaveBeenCalledTimes(1);
    expect(mocks.post).toHaveBeenCalledWith("/api/receipts/draft-image?location_id=loc-a", expect.any(FormData));
    expect(mocks.post.mock.calls[0][1].get("file")).toBe(file);
    mocks.post.mockResolvedValueOnce({ data: { line_count: 1 } });
    screen.getAllByRole("checkbox").forEach((checkbox) => {
      if (!(checkbox as HTMLInputElement).disabled && !(checkbox as HTMLInputElement).checked)
        fireEvent.click(checkbox);
    });
    fireEvent.click(screen.getByRole("button", { name: "Commit to Inventory" }));
    await waitFor(() =>
      expect(mocks.post).toHaveBeenLastCalledWith(
        "/api/receipts/drafts/draft-1/commit",
        expect.objectContaining({ source_filename: "receipt.jpg", location_id: "loc-a" })
      )
    );
  });
  it("blocks committing a draft after the selected location changes", async () => {
    await preview();
    mocks.location = "loc-b";
    fireEvent.change(screen.getByLabelText("Barcode"), { target: { value: "123" } });
    expect(screen.getByRole("button", { name: "Commit to Inventory" })).toBeDisabled();
    expect(screen.getByText("Location changed. Preview again before committing.")).toBeInTheDocument();
    expect(mocks.post).toHaveBeenCalledTimes(1);
  });

  it("does not commit excluded lines", async () => {
    await preview([matched, unmatched]);
    fireEvent.click(screen.getByRole("button", { name: "Exclude line 2" }));
    mocks.post.mockResolvedValueOnce({ data: { line_count: 1 } });
    screen.getAllByRole("checkbox").forEach((checkbox) => {
      if (!(checkbox as HTMLInputElement).disabled && !(checkbox as HTMLInputElement).checked)
        fireEvent.click(checkbox);
    });
    fireEvent.click(screen.getByRole("button", { name: "Commit to Inventory" }));
    await waitFor(() =>
      expect(mocks.post).toHaveBeenLastCalledWith(
        "/api/receipts/drafts/draft-1/commit",
        expect.objectContaining({
          lines: [
            expect.objectContaining({ matched_item_id: "milk", excluded: false }),
            expect.objectContaining({ excluded: true }),
          ],
        })
      )
    );
  });
  it("ignores an image preview arriving after a location change and disables upload while busy", async () => {
    let resolve!: (value: unknown) => void;
    mocks.post.mockReturnValueOnce(
      new Promise((done) => {
        resolve = done;
      })
    );
    renderWithProviders(<IngestPage />);
    fireEvent.change(screen.getByLabelText("Receipt image"), {
      target: { files: [new File(["image"], "receipt.jpg", { type: "image/jpeg" })] },
    });
    await waitFor(() => expect(screen.getByLabelText("Receipt image")).toBeDisabled());
    mocks.location = "loc-b";
    // Parent location changes rerender the page, even while its own controls are disabled.
    fireEvent.change(screen.getByLabelText("Barcode"), { target: { value: "123" } });
    resolve({ data: { lines: [matched], line_count: 1, draft_id: "draft-1", token: "draft-token" } });
    await waitFor(() =>
      expect(mocks.status).toHaveBeenCalledWith("Location changed. Preview the receipt again before committing.")
    );
    expect(screen.queryByLabelText("Item name 1")).not.toBeInTheDocument();
    expect(mocks.post).toHaveBeenCalledTimes(1);
  });

  it("lets users choose a destination on intake to enable text previews and photo uploads", async () => {
    mocks.location = null;
    renderWithProviders(<IngestPage />);
    fireEvent.change(screen.getByLabelText("Receipt text"), { target: { value: "milk 2 each" } });
    expect(screen.getByRole("button", { name: "Preview" })).toBeDisabled();
    expect(screen.getByLabelText("Receipt image")).toBeDisabled();
    await userEvent.click(screen.getByLabelText("Selected location", { selector: "input" }));
    await userEvent.click(screen.getByRole("option", { name: "Kitchen" }));
    expect(screen.getByRole("button", { name: "Preview" })).toBeEnabled();
    expect(screen.getByLabelText("Receipt image")).toBeEnabled();
    mocks.post.mockResolvedValueOnce({
      data: { lines: [matched], line_count: 1, draft_id: "draft-1", token: "draft-token" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Preview" }));
    await screen.findByLabelText("Item name 1");
    expect(mocks.post).toHaveBeenCalledWith("/api/receipts/draft", expect.objectContaining({ location_id: "loc-a" }));
  });
});

describe("Advisory classifier review", () => {
  it("keeps human verification mandatory, shows low score and never auto selects", async () => {
    await preview([unmatched]);
    expect(screen.getByText(/not validated accuracy/)).toBeInTheDocument();
    mocks.post.mockResolvedValueOnce({
      data: {
        provider_requests: 1,
        annotations: [
          {
            line_id: "line-2",
            status: "ok",
            role: "stock",
            suggested_item_id: "milk",
            score: 0.65,
            below_threshold: true,
            threshold: 0.9,
            candidates: [{ id: "milk", name: "Milk" }],
          },
        ],
      },
    });
    fireEvent.click(screen.getByRole("button", { name: "Ask classifier (admin)" }));
    await screen.findByText(/below your 90% threshold/);
    expect(screen.getByText("Suggested match: Milk")).toBeInTheDocument();
    expect(screen.getByLabelText("Catalog item 1", { selector: "input" })).toHaveValue("");
    expect(screen.getByRole("checkbox")).not.toBeChecked();
    expect(screen.getByRole("button", { name: "Commit to Inventory" })).toBeDisabled();
    fireEvent.change(screen.getByLabelText("Item name 1"), { target: { value: "Changed source interpretation" } });
    expect(screen.queryByText(/below your 90% threshold/)).not.toBeInTheDocument();
  });
  it("keeps a manual path after unavailable classifier and can restore exclusions", async () => {
    await preview();
    mocks.post.mockRejectedValueOnce(new Error("provider unavailable"));
    fireEvent.click(screen.getByRole("button", { name: "Ask classifier (admin)" }));
    await waitFor(() =>
      expect(mocks.status).toHaveBeenCalledWith(
        "Classifier unavailable or admin sign-in required. You can finish this receipt manually."
      )
    );
    fireEvent.click(screen.getByRole("button", { name: "Exclude line 1" }));
    expect(screen.getByRole("button", { name: "Commit to Inventory" })).toBeDisabled();
    fireEvent.click(screen.getByRole("button", { name: "Restore line 1" }));
    fireEvent.click(screen.getByRole("checkbox"));
    expect(screen.getByRole("button", { name: "Commit to Inventory" })).toBeEnabled();
  });
  it("invalidates a previous human verification after amount edits", async () => {
    await preview();
    fireEvent.click(screen.getByRole("checkbox"));
    expect(screen.getByRole("button", { name: "Commit to Inventory" })).toBeEnabled();
    fireEvent.change(screen.getByLabelText("Quantity 1"), { target: { value: "7" } });
    expect(screen.getByRole("checkbox")).not.toBeChecked();
    expect(screen.getByRole("button", { name: "Commit to Inventory" })).toBeDisabled();
  });
});

it("commits remaining verified stock after clearing a name and excluding its source row", async () => {
  await preview([matched, unmatched]);
  fireEvent.change(screen.getByLabelText("Item name 2"), { target: { value: "" } });
  fireEvent.click(screen.getByLabelText("I verified line 1"));
  expect(screen.getByRole("button", { name: "Commit to Inventory" })).toBeDisabled();
  fireEvent.click(screen.getByRole("button", { name: "Exclude line 2" }));
  expect(screen.getByLabelText("Item name 2")).toBeDisabled();
  expect(screen.getByRole("button", { name: "Commit to Inventory" })).toBeEnabled();
  mocks.post.mockResolvedValueOnce({ data: { line_count: 1 } });
  fireEvent.click(screen.getByRole("button", { name: "Commit to Inventory" }));
  await waitFor(() => expect(mocks.status).toHaveBeenCalledWith("Committed 1 items to inventory"));
  expect(mocks.post).toHaveBeenLastCalledWith(
    "/api/receipts/drafts/draft-1/commit",
    expect.objectContaining({
      lines: [
        expect.objectContaining({ line_id: "line-1", confirmed: true, excluded: false }),
        expect.objectContaining({ line_id: "line-2", name: "", excluded: true, raw_line: "bread 1 each" }),
      ],
    })
  );
  expect(mocks.post).toHaveBeenCalledTimes(2);
});
