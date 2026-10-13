/// <reference types="vitest" />
import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";

// The dev server and the preview server both proxy /api to the backend, so the
// browser always sees a single origin and the cart cookie works without CORS.
// VITE_API_TARGET points the proxy at a different backend (used by the e2e run).
const apiTarget = process.env.VITE_API_TARGET ?? "http://127.0.0.1:8000";
const proxy = {
  "/api": { target: apiTarget, changeOrigin: true },
};

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy,
  },
  preview: {
    port: 4173,
    proxy,
  },
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: ["./src/test/setup.ts"],
    css: false,
    restoreMocks: true,
    // Only the unit/component tests: the Playwright specs under e2e/ need a
    // real browser and must never run in the jsdom environment.
    include: ["src/**/*.test.{ts,tsx}"],
  },
});
