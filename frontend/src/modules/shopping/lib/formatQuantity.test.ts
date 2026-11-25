import { describe, expect, it } from "vitest";
import { formatQuantity } from "./formatQuantity";

describe("formatQuantity", () => {
  it("pluralises packages", () => {
    expect(formatQuantity(1, "package")).toBe("1 package");
    expect(formatQuantity("2.000", "package")).toBe("2 packages");
  });
  it("passes other units through", () => {
    expect(formatQuantity("1000.000", "ml")).toBe("1000 ml");
    expect(formatQuantity(3, "each")).toBe("3 each");
  });
});
