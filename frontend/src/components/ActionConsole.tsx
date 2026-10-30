import { createContext, type ReactNode, useContext, useEffect } from "react";
import { createPortal } from "react-dom";

/**
 * The Action Console is the pinned right-hand panel of the kiosk shell. Its
 * contents are screen-specific (Dashboard = "Quick Log", Inventory =
 * "Inventory actions"), so a page owns the console markup — including all of
 * its local state and mutations — and hands it to the shell for placement.
 *
 * The shell (AppLayout) provides a DOM container inside `AppShell.Aside`; a
 * page renders `<ActionConsole>` and its children are portaled into that
 * container while staying in the page's React tree. When no provider is present
 * (e.g. a page rendered standalone in a unit test), the children render inline
 * so the console remains testable.
 */
type ConsoleContextValue = {
  hasProvider: boolean;
  container: HTMLElement | null;
  setActive: (active: boolean) => void;
};

const ConsoleContext = createContext<ConsoleContextValue>({
  hasProvider: false,
  container: null,
  setActive: () => {},
});

export function ActionConsoleProvider({
  container,
  setActive,
  children,
}: {
  container: HTMLElement | null;
  setActive: (active: boolean) => void;
  children: ReactNode;
}) {
  return (
    <ConsoleContext.Provider value={{ hasProvider: true, container, setActive }}>
      {children}
    </ConsoleContext.Provider>
  );
}

export function useActionConsoleTarget() {
  return useContext(ConsoleContext);
}

export function ActionConsole({ children }: { children: ReactNode }) {
  const { hasProvider, container, setActive } = useContext(ConsoleContext);

  useEffect(() => {
    setActive(true);
    return () => setActive(false);
  }, [setActive]);

  // No shell (standalone render / tests): render inline so the console is
  // still present and interactive.
  if (!hasProvider) return <>{children}</>;
  // Shell present but container not yet mounted: wait a tick.
  if (!container) return null;
  return createPortal(children, container);
}
