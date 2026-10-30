import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import { VitePWA } from "vite-plugin-pwa";

const proxyTarget = process.env.VITE_API_PROXY_TARGET || "http://localhost:8000";

// Vite's dev server rejects requests whose Host header is a name it does not
// recognise (IP literals and localhost are always allowed). Set VITE_ALLOWED_HOSTS
// to a comma-separated hostname list to reach the dev server by name over the LAN,
// or to "all" to disable the check entirely.
const allowedHostsEnv = process.env.VITE_ALLOWED_HOSTS?.trim();
const allowedHosts =
  allowedHostsEnv === "all"
    ? true
    : allowedHostsEnv
      ? allowedHostsEnv.split(",").map((host) => host.trim()).filter(Boolean)
      : undefined;

export default defineConfig({
  resolve: {
    alias: {
      "@": "/src",
    },
  },
  plugins: [
    react(),
    VitePWA({
      registerType: "autoUpdate",
      devOptions: {
        enabled: false,
      },
      manifest: {
        name: "PantryOS",
        short_name: "PantryOS",
        start_url: "/",
        display: "standalone",
        background_color: "#ffffff",
        theme_color: "#ffffff",
        icons: [
          { src: "/pwa-192.png", sizes: "192x192", type: "image/png" },
          { src: "/pwa-512.png", sizes: "512x512", type: "image/png" },
        ],
      },
    }),
  ],
  server: {
    host: true,
    allowedHosts,
    proxy: {
      "/api": proxyTarget,
      "/health": proxyTarget,
      // So `curl <host>/openapi.json` reaches the API rather than returning
      // this app's HTML shell — see the /api/openapi.json note in app/main.py.
      "/openapi.json": proxyTarget,
      "/docs": proxyTarget,
      "/redoc": proxyTarget,
    },
  },
  test: {
    environment: "jsdom",
    setupFiles: ["./src/setupTests.ts"],
  },
});
