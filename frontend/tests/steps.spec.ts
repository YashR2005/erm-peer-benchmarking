import { expect, test } from '@playwright/test';

test('the Steps panel follows the live step, opens an earlier one without interrupting, and survives a reload', async ({ page }) => {
  await page.goto('/#/new');
  await page.getByPlaceholder('e.g. City Developments').fill('City Developments');
  await page.getByRole('button', { name: 'Start benchmark' }).click();

  const steps = page.getByRole('region', { name: 'Steps' });
  await page.getByRole('button', { name: 'Steps', exact: true }).click();
  await expect(steps).toBeVisible();
  await expect(steps.getByRole('navigation', { name: 'Run steps' })).toBeVisible();
  for (const group of ['Research', 'Confirm scope', 'Analysis', 'Review findings', 'Export']) {
    await expect(steps.getByRole('heading', { name: group, exact: true })).toBeVisible();
  }

  // Mid-run: it follows the live step (a running step is marked "live").
  await expect(steps.getByText('Following live')).toBeVisible({ timeout: 15_000 });

  // Open an earlier step without interrupting the run.
  await steps.getByRole('button', { name: /Request/ }).first().click();
  await expect(steps.getByLabel('Details of Request')).toBeVisible();
  await expect(steps.getByRole('button', { name: 'Jump to live' })).toBeVisible();

  // The run carries on to the checkpoint underneath.
  await expect(page.getByRole('heading', { name: 'Peer candidates' })).toBeVisible({ timeout: 30_000 });
  await steps.getByRole('button', { name: /Client profile/ }).click();
  const profile = steps.getByLabel(/Details of Client profile/);
  await expect(profile.getByText('Facts and their sources')).toBeVisible();
  await expect(profile.getByText('unsourced', { exact: true })).toBeVisible();
  await profile.getByRole('button', { name: 'Show raw' }).click();
  await expect(profile.locator('pre.step-raw').last()).toContainText('City Developments Limited');

  // Reload: everything comes back from disk.
  await page.reload();
  await page.getByRole('button', { name: 'Steps', exact: true }).click();
  await steps.getByRole('button', { name: /Peer candidates/ }).click();
  await expect(steps.getByLabel('Details of Peer candidates').getByText('Alpha Land').first()).toBeVisible();
});
