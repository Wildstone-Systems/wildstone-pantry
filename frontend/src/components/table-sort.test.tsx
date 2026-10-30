import { describe, expect, it } from "vitest";

import { applySort, toggleSort, type SortState } from "./table-sort";

type Row = { name: string; count: number; score: number | null };

const ROWS: Row[] = [
  { name: "Chili", count: 12, score: 0.5 },
  { name: "apple pie", count: 3, score: null },
  { name: "Bread", count: 7, score: 1 },
];

const ACCESSORS = {
  name: (r: Row) => r.name.toLowerCase(),
  count: (r: Row) => r.count,
  score: (r: Row) => r.score,
};

describe("toggleSort", () => {
  it("starts ascending, then flips on the same column", () => {
    let s: SortState = null;
    s = toggleSort(s, "name");
    expect(s).toEqual({ key: "name", dir: "asc" });
    s = toggleSort(s, "name");
    expect(s).toEqual({ key: "name", dir: "desc" });
  });

  it("resets to ascending when switching columns", () => {
    const s = toggleSort({ key: "name", dir: "desc" }, "count");
    expect(s).toEqual({ key: "count", dir: "asc" });
  });
});

describe("applySort", () => {
  it("sorts text case-insensitively", () => {
    const out = applySort(ROWS, { key: "name", dir: "asc" }, ACCESSORS);
    expect(out.map((r) => r.name)).toEqual(["apple pie", "Bread", "Chili"]);
  });

  it("sorts numbers numerically, not as strings", () => {
    // A string sort would put 12 before 3.
    const out = applySort(ROWS, { key: "count", dir: "asc" }, ACCESSORS);
    expect(out.map((r) => r.count)).toEqual([3, 7, 12]);
  });

  it("groups nulls at one end instead of scattering them", () => {
    const asc = applySort(ROWS, { key: "score", dir: "asc" }, ACCESSORS);
    expect(asc[0].score).toBeNull();
    const desc = applySort(ROWS, { key: "score", dir: "desc" }, ACCESSORS);
    expect(desc[desc.length - 1].score).toBeNull();
  });

  it("leaves rows untouched with no sort or an unknown key", () => {
    expect(applySort(ROWS, null, ACCESSORS)).toEqual(ROWS);
    expect(applySort(ROWS, { key: "nope", dir: "asc" }, ACCESSORS)).toEqual(ROWS);
  });

  it("does not mutate the input array", () => {
    const original = [...ROWS];
    applySort(ROWS, { key: "name", dir: "desc" }, ACCESSORS);
    expect(ROWS).toEqual(original);
  });
});
