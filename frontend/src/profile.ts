// Active household profile for the kiosk (ADR 0004): who everyday actions are
// attributed to. Persisted per-device so the kitchen tablet remembers the last
// person. Read outside React by the axios interceptor, so it lives here (like
// auth.ts) rather than only in context.
const ACTIVE_PROFILE_STORAGE_KEY = "pantry_active_profile_id";

export function getActiveProfileId(): string | null {
  if (typeof window === "undefined") return null;
  const raw = window.localStorage.getItem(ACTIVE_PROFILE_STORAGE_KEY);
  return raw && raw.trim().length > 0 ? raw : null;
}

export function setActiveProfileId(profileId: string | null) {
  if (typeof window === "undefined") return;
  if (!profileId) {
    window.localStorage.removeItem(ACTIVE_PROFILE_STORAGE_KEY);
  } else {
    window.localStorage.setItem(ACTIVE_PROFILE_STORAGE_KEY, profileId);
  }
}
