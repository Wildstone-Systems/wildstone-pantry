import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { renderWithProviders } from "@/test-utils";

const apiGet = vi.fn(async () => ({ data: { locations: [] } }));
const apiPost = vi.fn(async () => ({ data: { id: "loc-1", name: "Pantry", description: null, created_at: "2026-02-14T00:00:00Z" } }));

vi.mock("@/api/client", () => ({
  api: { get: apiGet, post: apiPost },
}));

describe("AddLocationPage (RHF + Zod)", () => {
  it("disables submit until the name is valid, then posts the trimmed value", async () => {
    const user = userEvent.setup();
    const { AddLocationPage } = await import("./AddLocationPage");

    renderWithProviders(<AddLocationPage />, { initialEntries: ["/catalog/add-location"] });

    const submit = screen.getByRole("button", { name: "Create Location" });
    expect(submit).toBeDisabled();

    await user.type(screen.getByLabelText(/Location name/), "  Pantry  ");
    await waitFor(() => expect(submit).toBeEnabled());

    await user.click(submit);

    await waitFor(() => expect(apiPost).toHaveBeenCalledTimes(1));
    expect(apiPost).toHaveBeenCalledWith("/api/locations", { name: "Pantry", description: null });
  });
});
