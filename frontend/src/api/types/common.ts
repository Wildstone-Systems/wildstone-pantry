/**
 * Cross-cutting API contract types (not domain DTOs — those live in each
 * module's hooks). Hand-mirrored from the backend contract.
 */

/** Structured error envelope returned by the API on failures. */
export type ApiError = {
  detail: string;
  errors?: Array<{ field?: string; message: string }>;
  request_id?: string;
};

export function isApiError(value: unknown): value is ApiError {
  return typeof value === "object" && value !== null && typeof (value as ApiError).detail === "string";
}

/** A page of results plus the total count (from header pagination). */
export type Page<T> = {
  items: T[];
  total: number;
};
