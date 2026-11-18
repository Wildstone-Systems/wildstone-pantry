const TOKEN_STORAGE_KEY = "auth_token";

export function getToken(): string | null {
  if (typeof window === "undefined") return null;
  return window.localStorage.getItem(TOKEN_STORAGE_KEY);
}

export function setToken(token: string) {
  if (typeof window === "undefined") return;
  window.localStorage.setItem(TOKEN_STORAGE_KEY, token);
}

export function clearToken() {
  if (typeof window === "undefined") return;
  window.localStorage.removeItem(TOKEN_STORAGE_KEY);
}


// A paired device's key (ADR 0018 §8) lives apart from the human login token:
// it identifies the *device* for everyday use and must survive an admin
// logging in or out, or a login session expiring.
const DEVICE_KEY_STORAGE_KEY = "device_key";

export function getDeviceKey(): string | null {
  if (typeof window === "undefined") return null;
  return window.localStorage.getItem(DEVICE_KEY_STORAGE_KEY);
}

export function setDeviceKey(key: string) {
  if (typeof window === "undefined") return;
  window.localStorage.setItem(DEVICE_KEY_STORAGE_KEY, key);
}

export function clearDeviceKey() {
  if (typeof window === "undefined") return;
  window.localStorage.removeItem(DEVICE_KEY_STORAGE_KEY);
}

/** The credential to send: a human login wins, else the device key. */
export function getRequestCredential(): string | null {
  return getToken() ?? getDeviceKey();
}
