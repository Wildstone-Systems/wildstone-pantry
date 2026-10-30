import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import React from "react";
import ReactDOM from "react-dom/client";
import { RouterProvider } from "react-router-dom";

import { registerSW } from "virtual:pwa-register";

import { router } from "./routes";

// PWA (ADR 0018 §8): auto-updating service worker so tablets/phones get an
// installable, app-like PantryOS. registerType is "autoUpdate" — assets
// refresh silently on new deploys.
registerSW({ immediate: true });
import { AppStateProvider } from "./state/AppStateProvider";
import { AuthProvider } from "./state/AuthProvider";
import { ThemeModeProvider } from "./theme/ThemeModeProvider";

const queryClient = new QueryClient();

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <ThemeModeProvider>
      <QueryClientProvider client={queryClient}>
        <AuthProvider>
          <AppStateProvider>
            <RouterProvider router={router} />
          </AppStateProvider>
        </AuthProvider>
      </QueryClientProvider>
    </ThemeModeProvider>
  </React.StrictMode>,
);

