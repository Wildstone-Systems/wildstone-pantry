import type { AxiosResponse } from "axios";
import { describe, expect, it } from "vitest";

import { readPage } from "./pagination";
import { isApiError } from "./types/common";

function resWith(headers: Record<string, string>): AxiosResponse {
  return { headers } as unknown as AxiosResponse;
}

describe("readPage (header pagination)", () => {
  it("reads total from X-Total-Count", () => {
    const page = readPage(resWith({ "x-total-count": "42" }), [1, 2, 3]);
    expect(page).toEqual({ items: [1, 2, 3], total: 42 });
  });

  it("falls back to item count when header is absent", () => {
    const page = readPage(resWith({}), ["a", "b"]);
    expect(page).toEqual({ items: ["a", "b"], total: 2 });
  });

  it("falls back when header is non-numeric", () => {
    const page = readPage(resWith({ "x-total-count": "nope" }), ["a"]);
    expect(page.total).toBe(1);
  });
});

describe("isApiError", () => {
  it("recognizes the error envelope", () => {
    expect(isApiError({ detail: "boom" })).toBe(true);
    expect(isApiError({ detail: "x", errors: [{ message: "m" }], request_id: "r" })).toBe(true);
  });

  it("rejects non-envelopes", () => {
    expect(isApiError(null)).toBe(false);
    expect(isApiError({ message: "no detail" })).toBe(false);
    expect(isApiError("string")).toBe(false);
  });
});
