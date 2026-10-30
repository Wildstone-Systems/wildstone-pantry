import { cleanup, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { renderWithProviders } from "@/test-utils";

const apiPost = vi.fn();
const apiDelete = vi.fn();

vi.mock("@/api/client", () => ({
  api: { post: (...a: unknown[]) => apiPost(...a), delete: (...a: unknown[]) => apiDelete(...a) },
}));

import { RecipePhoto } from "./RecipePhoto";

const BASE = { recipeId: "r1", recipeName: "Chili" };

describe("RecipePhoto", () => {
  beforeEach(() => {
    apiPost.mockReset();
    apiDelete.mockReset();
    apiPost.mockResolvedValue({ data: { id: "r1", image_src: "/api/recipes/r1/image" } });
    apiDelete.mockResolvedValue({ data: null });
  });
  afterEach(cleanup);

  it("offers to add one when the recipe has no picture", async () => {
    renderWithProviders(<RecipePhoto {...BASE} imageSrc={null} hasUpload={false} />);
    expect(await screen.findByRole("button", { name: /add a photo/i })).toBeInTheDocument();
    expect(screen.queryByRole("img")).toBeNull();
  });

  it("uploads the chosen file as multipart", async () => {
    const user = userEvent.setup();
    const { container } = renderWithProviders(
      <RecipePhoto {...BASE} imageSrc={null} hasUpload={false} />,
    );

    const file = new File(["xx"], "dish.png", { type: "image/png" });
    await user.upload(container.querySelector('input[type="file"]') as HTMLInputElement, file);

    await waitFor(() => expect(apiPost).toHaveBeenCalled());
    const [url, body] = apiPost.mock.calls[0];
    expect(url).toBe("/api/recipes/r1/image");
    // FormData, not a base64 body — the backend re-encodes anyway, so inflating
    // the payload by a third on the way up buys nothing.
    expect(body).toBeInstanceOf(FormData);
    expect((body as FormData).get("file")).toBe(file);
  });

  it("shows the picture and lets you replace it", async () => {
    renderWithProviders(
      <RecipePhoto {...BASE} imageSrc="/api/recipes/r1/image" hasUpload />,
    );
    expect(await screen.findByRole("img", { name: "Chili" })).toHaveAttribute(
      "src",
      "/api/recipes/r1/image",
    );
    expect(screen.getByRole("button", { name: /replace/i })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /remove/i })).toBeInTheDocument();
  });

  it("cannot remove an imported picture — there is no upload to delete", async () => {
    renderWithProviders(
      <RecipePhoto {...BASE} imageSrc="https://example.test/stock.jpg" hasUpload={false} />,
    );
    await screen.findByRole("img", { name: "Chili" });
    expect(screen.getByRole("button", { name: /replace/i })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /remove/i })).toBeNull();
  });

  it("removes an uploaded picture", async () => {
    const user = userEvent.setup();
    renderWithProviders(<RecipePhoto {...BASE} imageSrc="/api/recipes/r1/image" hasUpload />);
    await user.click(await screen.findByRole("button", { name: /remove/i }));
    await waitFor(() => expect(apiDelete).toHaveBeenCalledWith("/api/recipes/r1/image"));
  });
});
