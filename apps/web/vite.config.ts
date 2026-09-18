import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import { VitePWA } from "vite-plugin-pwa";

export default defineConfig({
  plugins: [
    react(),
    VitePWA({
      registerType: "autoUpdate",
      injectRegister: null,
      includeAssets: ["icons/apple-touch-icon.png"],
      manifest: {
        name: "GreenPlan",
        short_name: "GreenPlan",
        description: "Automatic urban greening placement from CAD drawings",
        display: "standalone",
        start_url: "/",
        theme_color: "#0b0f14",
        background_color: "#0b0f14",
        icons: [
          { src: "/icons/icon-192.png", sizes: "192x192", type: "image/png" },
          { src: "/icons/icon-512.png", sizes: "512x512", type: "image/png" },
          { src: "/icons/icon-maskable-512.png", sizes: "512x512", type: "image/png", purpose: "maskable" },
        ],
      },
      workbox: {
        // Precache built assets (default globPatterns); SPA navigations fall
        // back to the shell, but API routes must always hit the network.
        navigateFallback: "/index.html",
        navigateFallbackDenylist: [/^\/api\//],
        runtimeCaching: [
          {
            // Uploads / generation / exports are never cached (MVP: no API
            // caching). Non-anchored RegExps only match same-origin URLs.
            urlPattern: /\/api\//,
            handler: "NetworkOnly",
          },
        ],
      },
    }),
  ],
  server: {
    port: 5173,
    proxy: {
      "/api": {
        target: "http://localhost:8000",
        changeOrigin: true,
      },
    },
  },
});
