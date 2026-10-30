import axios from "axios";

import { getToken } from "../auth";
import { getActiveProfileId } from "../profile";

const configuredBaseUrl = import.meta.env.VITE_API_BASE_URL as string | undefined;

export const api = axios.create({
  // Default to same-origin so Vite proxy (dev) or a reverse proxy (prod) can forward /api + /health.
  baseURL: configuredBaseUrl && configuredBaseUrl.trim().length > 0 ? configuredBaseUrl : "",
});

/**
 * Absolute base URL of the backend API, for display/snippets (e.g. the Agent
 * Keys connect guide). Honors VITE_API_BASE_URL when set — the UI origin is
 * not necessarily where the API lives — and falls back to same-origin.
 */
export function apiBaseUrl(): string {
  const configured = configuredBaseUrl?.trim();
  if (configured) {
    // Resolve relative values against the page origin; strip any trailing slash.
    return new URL(configured, window.location.origin).toString().replace(/\/$/, "");
  }
  return window.location.origin;
}

api.interceptors.request.use((config) => {
  const token = getToken();
  if (token) {
    config.headers.Authorization = `Bearer ${token}`;
  }

  // Kiosk attribution: tell the backend which household profile is acting so
  // inventory events are stamped with the right actor (ADR 0004).
  const profileId = getActiveProfileId();
  if (profileId) {
    (config.headers as any)["X-Household-Profile"] = profileId;
  }

  return config;
});
// Session-expiry (a 401 on a request that carried a bearer token) is handled by
// AuthProvider (ADR 0004); kiosk 401s (no token) are intentionally left alone.
