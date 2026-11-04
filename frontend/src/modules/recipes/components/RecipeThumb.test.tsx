import { cleanup, fireEvent } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { renderWithProviders } from "@/test-utils";

import { RecipeThumb } from "./RecipeThumb";

describe("RecipeThumb", () => {
  afterEach(cleanup);

  it("renders the picture when there is one", () => {
    const { container } = renderWithProviders(<RecipeThumb src="/api/recipes/r1/image" h={64} />);
    expect(container.querySelector("img")).toHaveAttribute("src", "/api/recipes/r1/image");
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
