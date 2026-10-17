import { test, expect } from './fixtures';

test('sign in on mobile and navigate the drawer, inbox, and settings', async ({ page, account }) => {
  await page.goto('/');
  await page.getByPlaceholder(/email address/).fill(account);
  await page.getByRole('button', { name: 'Dev Login', exact: true }).click();
  await expect(page.getByRole('button', { name: 'Open menu' })).toBeVisible();
  await expect(page.getByRole('dialog')).toHaveCount(0);
  await page.getByRole('button', { name: 'Open menu' }).click();
  await page.getByRole('button', { name: 'Open inbox' }).click();
  await expect(page).toHaveURL(/\/inbox$/);
  await expect(page.getByText(/No requests yet\./)).toBeVisible();
  await page.getByRole('tab', { name: 'Memories', exact: true }).click();
  await expect(page).toHaveURL(/\/inbox\/memories$/);
  await page.reload();
  await expect(page.getByRole('tab', { name: 'Memories' })).toHaveAttribute('aria-selected', 'true');
  await page.getByRole('button', { name: 'Open menu' }).click();
  await page.getByRole('button', { name: 'Open settings' }).click();
  await page.getByRole('button', { name: 'Appearance', exact: true }).click();
  await page.getByRole('radio', { name: /^Dark/ }).click();
  await expect(page.locator('html')).toHaveAttribute('data-theme', 'dark');
  await page.getByRole('button', { name: 'Back to settings sections' }).click();
  await page.getByRole('button', { name: 'Sign Out', exact: true }).click();
  await page.getByRole('button', { name: 'Logout', exact: true }).click();
  await expect(page.getByText('AI-powered assistant')).toBeVisible();
});
