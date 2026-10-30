import { fireEvent, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { renderWithProviders } from "./test-utils";

const openRow = {
  id: "fb-open",
  user_id: "u-1",
  category: "bug-ui",
  route: "/inventory",
  message: "Summary:\nSomething broke",
  status: "open",
  created_at: "2026-02-14T00:00:00.000Z",
};

const resolvedRow = {
  id: "fb-resolved",
  user_id: "u-1",
  category: "feature-ui",
  route: "/dashboard",
  message: "Summary:\nAdd quick actions",
  status: "resolved",
  created_at: "2026-02-13T00:00:00.000Z",
};

const apiGetSpy = vi.fn(async (url: string) => {
  const u = String(url);
  if (u.includes("status=open")) return { data: { feedback: [openRow] } };
  if (u.includes("status=resolved")) return { data: { feedback: [resolvedRow] } };
  return { data: { feedback: [openRow, resolvedRow] } };
});

vi.mock("./api/client", () => ({
  api: {
    get: apiGetSpy,
    patch: vi.fn(async () => ({ data: {} })),
  },
}));

describe("Phase 20 feedback inbox filter", () => {
  it("filters feedback by status via the API", async () => {
    const { FeedbackPage } = await import("./modules/feedback/pages/FeedbackPage");

    renderWithProviders(<FeedbackPage />, { initialEntries: ["/feedback"] });

    await waitFor(() => expect(apiGetSpy).toHaveBeenCalled());
    expect(String(apiGetSpy.mock.calls[0]?.[0])).toContain("/api/feedback?limit=50&status=open");
    expect(await screen.findByText("BUG-UI | open")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("radio", { name: "Resolved" }));
    await waitFor(() => expect(apiGetSpy).toHaveBeenCalledTimes(2));
    expect(String(apiGetSpy.mock.calls[1]?.[0])).toContain("/api/feedback?limit=50&status=resolved");
    expect(await screen.findByText("FEATURE-UI | resolved")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("radio", { name: "All" }));
    await waitFor(() => expect(apiGetSpy).toHaveBeenCalledTimes(3));
    expect(String(apiGetSpy.mock.calls[2]?.[0])).toContain("/api/feedback?limit=50");
    expect(String(apiGetSpy.mock.calls[2]?.[0])).not.toContain("status=");
    expect(await screen.findByText("BUG-UI | open")).toBeInTheDocument();
    expect(await screen.findByText("FEATURE-UI | resolved")).toBeInTheDocument();
  });
});

