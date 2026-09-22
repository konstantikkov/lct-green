import { defineConfig } from "@playwright/test";

/**
 * GreenPlan e2e / screenshot suite. Runs on the deployment host (Playwright
 * is not runnable in the dev sandbox). Target via:
 *   PW_BASE_URL=https://13-61-189-48.sslip.io npx playwright test
 * Defaults to the local Vite dev server.
 */
const baseURL = process.env.PW_BASE_URL ?? "http://localhost:5173";

export default defineConfig({
  testDir: ".",
  timeout: 300_000, // generation runs can take minutes on the small host
  expect: { timeout: 15_000 },
  // The demo flow mutates shared backend state — run serially.
  fullyParallel: false,
  workers: 1,
  retries: 0,
  reporter: [["list"], ["html", { open: "never", outputFolder: "pw-report" }]],
  use: {
    baseURL,
    ignoreHTTPSErrors: true, // sslip.io self-managed cert setups
    screenshot: "only-on-failure",
    trace: "retain-on-failure",
  },
  projects: [
    { name: "1366x768", use: { viewport: { width: 1366, height: 768 } } },
    { name: "1440x900", use: { viewport: { width: 1440, height: 900 } } },
    { name: "1920x1080", use: { viewport: { width: 1920, height: 1080 } } },
  ],
});
