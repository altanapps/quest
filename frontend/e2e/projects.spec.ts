import { test, expect, closeOnboarding } from './fixtures';

test('create a project and upload, preview, and reload its workspace file', async ({ signedIn: page }) => {
  await page.getByRole('button', { name: 'Create Project', exact: true }).click();
  const modal = page.getByRole('dialog');
  await expect(modal.getByRole('button', { name: 'Create Project' })).toBeDisabled();
  await modal.getByLabel('Project Name', { exact: true }).fill('E2E Research');
  const created = page.waitForResponse((response) => response.url().endsWith('/app/api/projects') && response.request().method() === 'POST');
  await modal.getByRole('button', { name: 'Create Project' }).click();
  const response = await created;
  expect(response.ok()).toBeTruthy();
  const { id: projectId } = await response.json() as { id: string };
  await expect(modal).toHaveCount(0);
  await expect(page.getByText('E2E Research', { exact: true }).first()).toBeVisible();

  const files = page.locator('.file-browser').filter({ has: page.getByRole('heading', { name: 'Project Files', exact: true }) });
  await expect(files.getByRole('button', { name: 'Upload Files' })).toBeEnabled();
  const chooser = page.waitForEvent('filechooser');
  await files.getByRole('button', { name: 'Upload Files' }).click();
  await (await chooser).setFiles({
    name: 'research.txt',
    mimeType: 'text/plain',
    buffer: Buffer.from('Workspace upload verified by Playwright.'),
  });
  await files.getByText('research.txt', { exact: true }).click();
  await expect(page.getByRole('dialog').getByText('Workspace upload verified by Playwright.', { exact: true })).toBeVisible();
  await page.keyboard.press('Escape');
  // A project chat gives this file space a durable, reloadable route.
  const chat = await page.request.post(`/app/api/projects/${projectId}/conversations`);
  expect(chat.ok()).toBeTruthy();
  const { id } = await chat.json() as { id: string };
  await page.goto(`/projects/${projectId}/${id}`);
  await closeOnboarding(page);
  await expect(files.getByText('research.txt', { exact: true })).toBeVisible();
  await files.getByText('research.txt', { exact: true }).click();
  await expect(page.getByRole('dialog').getByText('Workspace upload verified by Playwright.', { exact: true })).toBeVisible();
});
