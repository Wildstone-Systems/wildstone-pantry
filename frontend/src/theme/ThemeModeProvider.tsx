import "@mantine/core/styles.css";
import "./global.css";

import { MantineProvider } from "@mantine/core";
import { createContext, type ReactNode, useContext, useMemo, useState } from "react";

import { theme } from "./theme";

type ThemeMode = "light" | "dark";

type ThemeModeContextValue = {
  mode: ThemeMode;
  setMode: (mode: ThemeMode) => void;
  toggleMode: () => void;
};

const ThemeModeContext = createContext<ThemeModeContextValue | null>(null);

const STORAGE_KEY = "pantry_theme_mode";

function getInitialMode(initialMode?: ThemeMode): ThemeMode {
  if (initialMode) return initialMode;
  if (typeof window === "undefined") return "light";

  const stored = window.localStorage.getItem(STORAGE_KEY);
  return stored === "dark" ? "dark" : "light";
}

export function ThemeModeProvider({ children, initialMode }: { children: ReactNode; initialMode?: ThemeMode }) {
  const [mode, setModeState] = useState<ThemeMode>(() => getInitialMode(initialMode));

  const setMode = (nextMode: ThemeMode) => {
    setModeState(nextMode);
    if (typeof window !== "undefined") {
      window.localStorage.setItem(STORAGE_KEY, nextMode);
    }
  };

  const toggleMode = () => setMode(mode === "light" ? "dark" : "light");

  const contextValue = useMemo(
    () => ({
      mode,
      setMode,
      toggleMode,
    }),
    [mode],
  );

  return (
    <ThemeModeContext.Provider value={contextValue}>
      <MantineProvider theme={theme} forceColorScheme={mode}>
        {children}
      </MantineProvider>
    </ThemeModeContext.Provider>
  );
}

export function useThemeMode() {
  const context = useContext(ThemeModeContext);
  if (!context) {
    throw new Error("useThemeMode must be used within ThemeModeProvider");
  }
  return context;
}

