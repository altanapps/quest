import { randomUUID } from 'node:crypto';
import { test, expect, closeOnboarding, login } from './fixtures';

for (const kind of ['Doc', 'Table']) {
  test(`create a ${kind.toLowerCase()} through its form and reopen it from the list`, async ({ page }) => {
    const route = `/${kind.toLowerCase()}s`;
    await login(page, 'alice@quest.local', route);
    await page.getByRole('button', { name: `New ${kind}`, exact: true }).click();
    const dialog = page.getByRole('dialog');
    const create = dialog.getByRole('button', { name: `Create ${kind}`, exact: true });
    await expect(create).toBeDisabled();
    const title = `E2E ${kind} ${randomUUID()}`;
    await dialog.getByLabel('Title', { exact: true }).fill(title);
    await create.click();
    await expect(page).toHaveURL(new RegExp(`${route}/[^/]+$`));
    await expect(page.getByRole('button', { name: title, exact: true })).toBeVisible();
    const viewerURL = page.url();
    await page.reload();
    await closeOnboarding(page);
    await expect(page.getByRole('button', { name: title, exact: true })).toBeVisible();
    await page.goto(route);
    await closeOnboarding(page);
    await page.getByRole('link').filter({ hasText: title }).click();
    await expect(page).toHaveURL(viewerURL);
    await expect(page.getByRole('button', { name: title, exact: true })).toBeVisible();
  });
}
