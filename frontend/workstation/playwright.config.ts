import { defineConfig, devices } from '@playwright/test'

/**
 * PharmPilot Workstation — browser-automated functional/UI audit
 * ===============================================================
 * API   → http://localhost:8001  (must be started via `bash scripts/dev.sh`)
 * Front → http://localhost:3001
 *
 * Auth: tests/e2e/auth.setup.ts logs in as `pharmacist` once and persists
 * storage state (access_token + role + pharmacy_id in localStorage) so every
 * spec starts already signed in — no UI login per test.
 */
export default defineConfig({
  testDir: './tests/e2e',
  outputDir: './tests/e2e/.artifacts',
  fullyParallel: false,        // serial: shared dev API/DB state, avoid races
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 1 : 0,
  workers: 1,
  reporter: [
    ['list'],
    ['html', { outputFolder: './tests/e2e/.report', open: 'never' }],
  ],
  timeout: 45_000,
  expect: { timeout: 10_000 },

  use: {
    baseURL: 'http://localhost:3001',
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
    video: 'off',
    actionTimeout: 10_000,
    navigationTimeout: 20_000,
  },

  projects: [
    {
      name: 'setup',
      testMatch: /auth\.setup\.ts/,
    },
    {
      name: 'chromium',
      use: {
        ...devices['Desktop Chrome'],
        viewport: { width: 1920, height: 1080 },
        storageState: './tests/e2e/.auth/pharmacist.json',
      },
      dependencies: ['setup'],
    },
  ],
})
