import { test, expect, closeOnboarding } from './fixtures';

test('sign in through the account picker, persist a session, and sign out', async ({ page }) => {
  await page.goto('/');
  await expect(page.getByText('AI-powered assistant')).toBeVisible();
  expect((await page.request.get('/app/api/me')).status()).toBe(401);

  await page.getByRole('button', { name: /Bob Rivera.*bob@quest.local/ }).click();
  await closeOnboarding(page);
  await expect(page.getByText('bob@quest.local', { exact: true })).toBeVisible();
  await page.reload();
  await closeOnboarding(page);
  await expect(page.getByText('bob@quest.local', { exact: true })).toBeVisible();

  await page.getByRole('button', { name: 'Open settings', exact: true }).click();
  await page.getByRole('button', { name: 'Sign Out', exact: true }).click();
  await page.getByRole('button', { name: 'Logout', exact: true }).click();
  await expect(page.getByText('AI-powered assistant')).toBeVisible();
  expect((await page.request.get('/app/api/me')).status()).toBe(401);
  await page.reload();
  await expect(page.getByRole('button', { name: 'Dev Login', exact: true })).toBeVisible();
});

test('sign in with a fresh email and keep sending disabled without credentials', async ({ page, account }) => {
  await page.goto('/');
  await page.getByPlaceholder(/email address/).fill(account);
  await page.getByRole('button', { name: 'Dev Login', exact: true }).click();
  await closeOnboarding(page);
  await expect(page.getByText(account, { exact: true })).toBeVisible();
  await expect(page.getByText('No LLM credentials', { exact: false })).toBeVisible();
  await expect(page.getByRole('button', { name: 'Send', exact: true })).toBeDisabled();
});
