import { defineConfig, devices } from '@playwright/test';

const port = Number(process.env.QUEST_E2E_PORT ?? 9360);
if (!Number.isInteger(port) || port < 1024 || port >= 65535) {
  throw new Error('QUEST_E2E_PORT must be an integer from 1024 to 65534');
}
const baseURL = `http://127.0.0.1:${port}`;

export default defineConfig({
  testDir: './e2e',
  fullyParallel: true,
  forbidOnly: Boolean(process.env.CI),
  retries: process.env.CI ? 1 : 0,
  workers: 2,
  reporter: [['list'], ['html', { open: 'never' }]],
  use: {
    baseURL,
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
    serviceWorkers: 'block',
  },
  projects: [
    {
      name: 'chromium',
      use: { ...devices['Desktop Chrome'] },
      testIgnore: 'mobile.spec.ts',
    },
    {
      name: 'mobile-chromium',
      use: { ...devices['Pixel 7'] },
      testMatch: 'mobile.spec.ts',
    },
  ],
  webServer: {
    command: `uv run --project .. python e2e/server.py --port ${port}`,
    url: `${baseURL}/auth/dev-accounts`,
    reuseExistingServer: false,
    timeout: 120_000,
    gracefulShutdown: { signal: 'SIGTERM', timeout: 10_000 },
  },
});
