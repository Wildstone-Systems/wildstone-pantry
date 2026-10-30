import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, type RenderResult } from "@testing-library/react";
import type { ReactElement } from "react";
import { MemoryRouter } from "react-router-dom";

import { AppStateProvider } from "./state/AppStateProvider";
import { ThemeModeProvider } from "./theme/ThemeModeProvider";

export function renderWithProviders(ui: ReactElement, { initialEntries }: { initialEntries?: string[] } = {}): RenderResult {
  const queryClient = new QueryClient({
    defaultOptions: {
      queries: { retry: false },
    },
  });

  return render(
    <ThemeModeProvider initialMode="light">
      <QueryClientProvider client={queryClient}>
        <AppStateProvider>
          <MemoryRouter initialEntries={initialEntries}>{ui}</MemoryRouter>
        </AppStateProvider>
      </QueryClientProvider>
    </ThemeModeProvider>,
  );
}

