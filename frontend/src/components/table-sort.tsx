import { Table, Text } from "@mantine/core";

/**
 * Client-side column sorting shared by the data tables.
 *
 * Extracted from InventoryPage so every sortable table behaves the same way —
 * same asc/desc cycle, same indicator, same `aria-sort` — instead of each page
 * growing its own near-copy.
 *
 * Sort keys are plain strings rather than a generic union: each table passes
 * its own accessor map, and keeping the key loose is what lets one component
 * serve tables with completely different columns.
 */
export type SortDir = "asc" | "desc";
export type SortState = { key: string; dir: SortDir } | null;

/** First click sorts ascending; clicking the active column flips direction. */
export function toggleSort(current: SortState, key: string): SortState {
  if (!current || current.key !== key) return { key, dir: "asc" };
  return { key, dir: current.dir === "asc" ? "desc" : "asc" };
}

export function applySort<T>(
  rows: T[],
  sort: SortState,
  accessors: Record<string, (row: T) => string | number | null>,
): T[] {
  if (!sort) return rows;
  const accessor = accessors[sort.key];
  if (!accessor) return rows;
  const sorted = [...rows].sort((a, b) => {
    const av = accessor(a);
    const bv = accessor(b);
    // Nulls sort together at one end rather than scattering unpredictably.
    if (av === null && bv === null) return 0;
    if (av === null) return -1;
    if (bv === null) return 1;
    if (typeof av === "number" && typeof bv === "number") return av - bv;
    return String(av).localeCompare(String(bv));
  });
  return sort.dir === "asc" ? sorted : sorted.reverse();
}

/** Clickable column header with an asc/desc indicator. */
export function SortableTh({
  label,
  sortKey,
  sort,
  onSort,
  w,
}: {
  label: string;
  sortKey: string;
  sort: SortState;
  onSort: (key: string) => void;
  /** Number = px; string for CSS widths ("22%") in fixed-layout tables. */
  w?: number | string;
}) {
  const active = sort?.key === sortKey;
  const arrow = active ? (sort.dir === "asc" ? "▲" : "▼") : "";
  return (
    <Table.Th
      w={w}
      onClick={() => onSort(sortKey)}
      // nowrap is what keeps a two-word heading ("Can make now") on one line
      // instead of wrapping and doubling every header's height.
      style={{ cursor: "pointer", userSelect: "none", whiteSpace: "nowrap" }}
      aria-sort={active ? (sort.dir === "asc" ? "ascending" : "descending") : "none"}
    >
      {label}
      {arrow ? (
        <Text component="span" size="xs" ml={4} c="dimmed">
          {arrow}
        </Text>
      ) : null}
    </Table.Th>
  );
}
