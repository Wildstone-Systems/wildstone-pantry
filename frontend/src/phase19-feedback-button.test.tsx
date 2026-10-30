import { fireEvent, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { renderWithProviders } from "./test-utils";

const apiPostSpy = vi.fn(async () => ({ data: {} }));

vi.mock("./api/client", () => ({
  api: {
    post: apiPostSpy,
  },
}));

describe("Phase 19 feedback button (Mantine modal)", () => {
  it("submits structured feedback with category and meta", async () => {
    const { FeedbackButton } = await import("./components/FeedbackButton");

    renderWithProviders(<FeedbackButton />, { initialEntries: ["/inventory?location=loc-1"] });

    fireEvent.click(screen.getByRole("button", { name: "Open feedback form" }));

    const summaryField = await screen.findByLabelText(/Summary/);
    fireEvent.change(summaryField, { target: { value: "Sidebar overlaps content" } });
    fireEvent.change(await screen.findByLabelText(/Steps to Reproduce/), {
      target: { value: "1. Collapse\n2. Try expand" },
    });
    fireEvent.change(await screen.findByLabelText(/Expected/), { target: { value: "Toggle visible" } });
    fireEvent.change(await screen.findByLabelText(/Actual/), { target: { value: "Toggle hidden" } });

    fireEvent.click(screen.getByRole("button", { name: "Submit" }));

    await waitFor(() => expect(apiPostSpy).toHaveBeenCalledTimes(1));
    const [path, payload] = apiPostSpy.mock.calls[0] as unknown as [
      string,
      { category: string; route: string; message: string },
    ];
    expect(path).toBe("/api/feedback");
    expect(payload.category).toBe("bug-ui");
    expect(payload.route).toBe("/inventory?location=loc-1");
    expect(payload.message).toContain("Summary:\nSidebar overlaps content");
    expect(payload.message).toContain("Meta:");
  });
});
