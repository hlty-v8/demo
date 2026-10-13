import { defineConfig, devices } from "@playwright/test";

/**
 * Browser end-to-end configuration.
 *
 * Two real servers are started for the run:
 *   * uvicorn        - the actual FastAPI app, on its own SQLite database;
 *   * vite preview   - the production build, proxying /api to that API.
 *
 * Nothing is mocked: the browser, the HTTP requests, the proxy and the cookie
 * are all real. `pnpm e2e` builds the frontend first (see package.json), so the
 * preview server always serves current code.
 */
const API_PORT = Number(process.env.E2E_API_PORT ?? 8010);
const WEB_PORT = Number(process.env.E2E_WEB_PORT ?? 4173);
const apiUrl = `http://127.0.0.1:${API_PORT}`;
const webUrl = `http://127.0.0.1:${WEB_PORT}`;

export default defineConfig({
  testDir: "./e2e",
  timeout: 30_000,
  expect: { timeout: 7_000 },
  fullyParallel: false,
  workers: 1,
  forbidOnly: Boolean(process.env.CI),
  retries: process.env.CI ? 1 : 0,
  reporter: process.env.CI ? [["list"], ["github"]] : [["list"]],
  use: {
    baseURL: webUrl,
    trace: "retain-on-failure",
  },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"] } }],
  webServer: [
    {
      command: `python -m uvicorn app.main:app --host 127.0.0.1 --port ${API_PORT} --log-level info`,
      cwd: "../backend",
      url: `${apiUrl}/api/health`,
      reuseExistingServer: !process.env.CI,
      timeout: 60_000,
      // Piped rather than ignored: if the server refuses to start, its own error
      // is what explains the generic "Timed out waiting ... from config.webServer".
      stdout: "pipe",
      stderr: "pipe",
      // A fresh database per run keeps the stock assertions exact; the files
      // live in the git-ignored app/backend/var/ directory.
      env: { PDP_DB_PATH: `var/e2e-${Date.now()}.sqlite3` },
    },
    {
      // --host 127.0.0.1 explicitly: `vite preview` otherwise binds whatever
      // "localhost" resolves to, which on some runners is ::1 only - and the
      // readiness probe below (and the browser) use 127.0.0.1.
      command: `pnpm preview --host 127.0.0.1 --port ${WEB_PORT} --strictPort`,
      url: webUrl,
      reuseExistingServer: !process.env.CI,
      timeout: 60_000,
      stdout: "pipe",
      stderr: "pipe",
      env: { VITE_API_TARGET: apiUrl },
    },
  ],
});
