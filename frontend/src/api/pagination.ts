import type { AxiosResponse } from "axios";

import type { Page } from "./types/common";

/**
 * Header-based pagination: read the total from `X-Total-Count` and pair it with
 * the already-extracted item array. Falls back to the array length when the
 * header is absent.
 */
export function readPage<T>(res: AxiosResponse, items: T[]): Page<T> {
  const header = res.headers["x-total-count"];
  const total = header !== undefined ? Number(header) : items.length;
  return { items, total: Number.isFinite(total) ? total : items.length };
}
