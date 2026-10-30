import type { AxiosError } from "axios";

type FastApiErrorPayload =
  | string
  | {
      detail?: string | Array<{ msg?: string; message?: string; loc?: unknown; type?: string }>;
    };

export function getApiErrorMessage(error: unknown, fallback = "Request failed"): string {
  const axiosError = error as AxiosError<FastApiErrorPayload>;
  const payload = axiosError.response?.data;

  if (!payload) {
    return fallback;
  }

  if (typeof payload === "string" && payload.trim().length > 0) {
    return payload;
  }

  if (typeof (payload as any).detail === "string" && (payload as any).detail.trim().length > 0) {
    return (payload as any).detail;
  }

  const detail = (payload as any).detail;
  if (Array.isArray(detail) && detail.length > 0) {
    const first = detail[0];
    const msg = first?.msg || first?.message;
    if (typeof msg === "string" && msg.trim().length > 0) {
      return msg;
    }
  }

  return fallback;
}

