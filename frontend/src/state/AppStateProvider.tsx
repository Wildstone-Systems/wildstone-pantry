import { createContext, type ReactNode, useContext, useEffect, useMemo, useState } from "react";

import { getActiveProfileId, setActiveProfileId as persistActiveProfileId } from "../profile";

type AppStateContextValue = {
  selectedLocationId: string | null;
  setSelectedLocationId: (locationId: string | null) => void;
  activeProfileId: string | null;
  setActiveProfileId: (profileId: string | null) => void;
  status: string;
  setStatus: (status: string) => void;
};

const AppStateContext = createContext<AppStateContextValue | null>(null);

const SELECTED_LOCATION_STORAGE_KEY = "pantry_selected_location_id";

function getInitialSelectedLocationId(): string | null {
  if (typeof window === "undefined") return null;
  const raw = window.localStorage.getItem(SELECTED_LOCATION_STORAGE_KEY);
  return raw && raw.trim().length > 0 ? raw : null;
}

export function AppStateProvider({ children }: { children: ReactNode }) {
  const [selectedLocationId, setSelectedLocationIdState] = useState<string | null>(() => getInitialSelectedLocationId());
  const [activeProfileId, setActiveProfileIdState] = useState<string | null>(() => getActiveProfileId());
  const [status, setStatusState] = useState("Ready");

  const setActiveProfileId = (profileId: string | null) => {
    setActiveProfileIdState(profileId);
    persistActiveProfileId(profileId);
  };

  const setSelectedLocationId = (locationId: string | null) => {
    setSelectedLocationIdState(locationId);
    if (typeof window !== "undefined") {
      try {
        if (!locationId) {
          window.localStorage.removeItem(SELECTED_LOCATION_STORAGE_KEY);
        } else {
          window.localStorage.setItem(SELECTED_LOCATION_STORAGE_KEY, locationId);
        }
      } catch {
        // ignore storage issues
      }
    }
  };

  useEffect(() => {
    // keep storage up to date if setSelectedLocationIdState is used directly
    if (typeof window === "undefined") return;
    try {
      if (!selectedLocationId) {
        window.localStorage.removeItem(SELECTED_LOCATION_STORAGE_KEY);
      } else {
        window.localStorage.setItem(SELECTED_LOCATION_STORAGE_KEY, selectedLocationId);
      }
    } catch {
      // ignore storage issues
    }
  }, [selectedLocationId]);

  const setStatus = (next: string) => setStatusState(next);

  const contextValue = useMemo(
    () => ({
      selectedLocationId,
      setSelectedLocationId,
      activeProfileId,
      setActiveProfileId,
      status,
      setStatus,
    }),
    [selectedLocationId, activeProfileId, status],
  );

  return <AppStateContext.Provider value={contextValue}>{children}</AppStateContext.Provider>;
}

export function useAppState() {
  const context = useContext(AppStateContext);
  if (!context) {
    throw new Error("useAppState must be used within AppStateProvider");
  }
  return context;
}

