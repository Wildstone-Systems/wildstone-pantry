import { cleanup, fireEvent, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { renderWithProviders } from "@/test-utils";

import { RecipeThumb } from "./RecipeThumb";

const apiGet = vi.fn();

vi.mock("@/api/client", () => ({
  api: { get: (...args: unknown[]) => apiGet(...args) },
}));

describe("RecipeThumb", () => {
  beforeEach(() => {
    apiGet.mockReset();
    // jsdom has no object URLs.
    URL.createObjectURL = vi.fn(() => "blob:recipe-r1");
    URL.revokeObjectURL = vi.fn();
  });
  afterEach(cleanup);

  it("loads our own API image through the signed-in client", async () => {
    apiGet.mockResolvedValue({ data: new Blob(["img"], { type: "image/jpeg" }) });
    const { container } = renderWithProviders(<RecipeThumb src="/api/recipes/r1/image" h={64} />);
    await waitFor(() => expect(container.querySelector("img")).toHaveAttribute("src", "blob:recipe-r1"));
    expect(apiGet).toHaveBeenCalledWith("/api/recipes/r1/image", { responseType: "blob" });
  });

  it("uses an external picture URL as-is", () => {
    const { container } = renderWithProviders(<RecipeThumb src="https://example.com/x.jpg" h={64} />);
    expect(container.querySelector("img")).toHaveAttribute("src", "https://example.com/x.jpg");
    expect(apiGet).not.toHaveBeenCalled();
  });

  it("shows the placeholder when there is no picture", () => {
    const { container } = renderWithProviders(<RecipeThumb src={null} h={64} />);
    expect(container.querySelector("img")).toBeNull();
    expect(container.querySelector(".pantry-photo")).not.toBeNull();
  });

  it("falls back to the placeholder when the picture fails to load", () => {
    const { container } = renderWithProviders(<RecipeThumb src="https://stale.example/x.jpg" h={64} />);
    fireEvent.error(container.querySelector("img")!);
    expect(container.querySelector("img")).toBeNull();
    expect(container.querySelector(".pantry-photo")).not.toBeNull();
  });
});
