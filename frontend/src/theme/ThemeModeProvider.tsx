import "@mantine/core/styles.css";
import "./global.css";

import { Accordion, Collapse, MantineProvider, mergeThemeOverrides } from "@mantine/core";
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

// Mantine's `env="test"` does not cover Collapse: it keeps its content hidden
// until an animation frame runs, so on slow CI runners a test can look for a
// button in an open card before it is visible. With no transition, an expanded
// Collapse renders its content at once; a collapsed one keeps it mounted
// but hidden (display: none), as Mantine 7 did. Accordion passes its own
// keepMountedMode ("activity") down to its Collapse, so it needs the same
// override: React defers updates inside a hidden Activity, which left stale
// text in closed panels after the test had moved on.
const testTheme = mergeThemeOverrides(theme, {
  components: {
    Collapse: Collapse.extend({ defaultProps: { transitionDuration: 0, keepMountedMode: "display-none" } }),
    Accordion: Accordion.extend({ defaultProps: { transitionDuration: 0, keepMountedMode: "display-none" } }),
  },
});

function getInitialMode(initialMode?: ThemeMode): ThemeMode {
  if (initialMode) return initialMode;
  if (typeof window === "undefined") return "light";

  const stored = window.localStorage.getItem(STORAGE_KEY);
  return stored === "dark" ? "dark" : "light";
}

export function ThemeModeProvider({
  children,
  initialMode,
  env,
}: {
  children: ReactNode;
  initialMode?: ThemeMode;
  /** "test" disables Mantine transitions and portals so tests see dropdown content at once. */
  env?: "default" | "test";
}) {
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
      <MantineProvider theme={env === "test" ? testTheme : theme} forceColorScheme={mode} env={env}>
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

