import { randomUUID } from 'node:crypto';
import { test as base, expect, type Page } from '@playwright/test';

export { expect };

/** Desktop local accounts open onboarding settings on every full page load. */
export async function closeOnboarding(page: Page) {
  await page.getByRole('button', { name: 'Close settings', exact: true }).click();
  await expect(page.getByRole('dialog')).toHaveCount(0);
}

export async function login(page: Page, email: string, path = '/') {
  const response = await page.request.post('/auth/dev-login', { data: { email } });
  expect(response.ok()).toBeTruthy();
  await page.goto(path);
  await closeOnboarding(page);
}

export const test = base.extend<{ account: string; signedIn: Page }>({
  account: async ({ browserName }, provide) => {
    await provide(`e2e-${browserName}-${randomUUID()}@quest.local`);
  },
  signedIn: async ({ page, account }, provide) => {
    await login(page, account);
    await provide(page);
  },
});
