import { cleanup, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { renderWithProviders } from "@/test-utils";

import { CalendarFeedModal } from "./CalendarFeedModal";

const apiGet = vi.fn();
const apiPost = vi.fn();
const apiDelete = vi.fn();
vi.mock("@/api/client", () => ({
  apiBaseUrl: () => "https://api.example.test",
  api: {
    get: (...a: unknown[]) => apiGet(...a),
    post: (...a: unknown[]) => apiPost(...a),
    delete: (...a: unknown[]) => apiDelete(...a),
  },
}));

describe("CalendarFeedModal", () => {
  beforeEach(() => {
    for (const m of [apiGet, apiPost, apiDelete]) m.mockReset();
  });
  afterEach(() => cleanup());

  it("makes a link and shows it once, on the API's address", async () => {
    apiGet.mockResolvedValue({ data: { enabled: false, created_at: null } });
    apiPost.mockResolvedValue({ data: { path: "/api/meal-plan/calendar.ics?token=mpcal_x", created_at: "" } });
    renderWithProviders(<CalendarFeedModal opened onClose={() => {}} />);

    expect(await screen.findByText("No calendar link yet.")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Make a link" }));
    expect(await screen.findByLabelText("Your calendar link")).toHaveValue(
      "https://api.example.test/api/meal-plan/calendar.ics?token=mpcal_x",
    );
  });

  it("turns an existing link off", async () => {
    apiGet.mockResolvedValue({ data: { enabled: true, created_at: "" } });
    apiDelete.mockResolvedValue({});
    renderWithProviders(<CalendarFeedModal opened onClose={() => {}} />);

    expect(await screen.findByText("A calendar link is on.")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Make a new link" })).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Turn off" }));
    await waitFor(() => expect(apiDelete).toHaveBeenCalledWith("/api/meal-plan/calendar-feed"));
  });
});
