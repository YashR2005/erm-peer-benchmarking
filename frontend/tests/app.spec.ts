import { expect, test } from '@playwright/test';
import type { Page } from '@playwright/test';

async function startBenchmark(page: Page) {
  await page.goto('/#/new');
  await page.getByPlaceholder('e.g. City Developments').fill('City Developments');
  await page.getByRole('button', { name: 'Start benchmark' }).click();
  await expect(page.getByRole('heading', { name: 'Peer candidates' })).toBeVisible({ timeout: 30_000 });
}

test('a benchmark runs from the start form to an exported workbook', async ({ page }) => {
  await startBenchmark(page);
  await expect(page.getByRole('heading', { name: 'City Developments Limited' }).first()).toBeVisible();
  await expect(page.getByText('Research finished. Check the peers and sources.')).toBeVisible();
  await page.screenshot({ path: 'test-results/scope.png', fullPage: true });

  // Checkpoint 1: three candidates, the client itself is excluded; drop Gamma.
  const toggles = page.getByRole('switch');
  await expect(toggles).toHaveCount(3);
  await page.getByRole('switch', { name: 'Use Gamma REIT' }).click();
  await expect(page.getByRole('switch', { name: 'Use Gamma REIT' })).toHaveAttribute('aria-checked', 'false');
  await expect(page.getByText('2 peers selected').first()).toBeVisible();
  await page.getByRole('button', { name: 'Details for Alpha Land' }).click();
  await expect(page.getByText('Alpha Land develops property in Singapore.')).toBeVisible();

  await page.getByRole('button', { name: 'Lock scope and run analysis' }).click();
  await expect(page.locator('.matrix')).toBeVisible({ timeout: 30_000 });
  await page.screenshot({ path: 'test-results/findings.png', fullPage: true });

  // Checkpoint 2: evidence behind a cell, then reject it.
  await page.getByRole('button', { name: /Cybersecurity: CDL discloses it/ }).click();
  const dialog = page.getByRole('dialog');
  await expect(dialog.getByText("A cyber attack could disrupt the Group's IT systems")).toBeVisible();
  await dialog.getByRole('button', { name: 'Reject' }).click();
  await expect(page.getByText('Quote rejected.')).toBeVisible();
  await dialog.getByRole('button', { name: 'Close dialog' }).click();
  await expect(page.getByRole('button', { name: /Cybersecurity: CDL discloses it/ })).toHaveCount(0);

  // Tabs.
  await page.getByRole('tab', { name: /Gaps/ }).click();
  await expect(page.getByText('Disclosed by peers, not by the client')).toBeVisible();
  await page.getByRole('tab', { name: /Category structure/ }).click();
  await expect(page.getByText('Alpha Land has a category outside the standard taxonomy: Safety & Security.')).toBeVisible();
  await page.getByLabel('Add a remark').fill('Both peers lack a Sustainability category.');
  await page.getByRole('button', { name: 'Add remark' }).click();
  await expect(page.locator('.remark')).toHaveText('Both peers lack a Sustainability category.');

  // Export downloads the workbook.
  const download = page.waitForEvent('download');
  await page.getByRole('button', { name: 'Export to Excel' }).click();
  expect((await download).suggestedFilename()).toMatch(/product-a\.xlsx$/);
  await expect(page.getByRole('link', { name: 'Latest workbook' })).toBeVisible();

  // The overview lists the run.
  await page.goto('/#/');
  await expect(page.locator('.run-table')).toContainText('City Developments Limited');
  await expect(page.locator('.run-table')).toContainText('Exported');
});

test('a theme can be renamed, removed and restored from its row menu', async ({ page }) => {
  await startBenchmark(page);
  await page.getByRole('button', { name: 'Lock scope and run analysis' }).click();
  await expect(page.locator('.matrix')).toBeVisible({ timeout: 30_000 });

  await page.getByRole('button', { name: 'Actions for Foreign exchange' }).click();
  await page.getByRole('menuitem', { name: 'Rename theme' }).click();
  await page.getByLabel('Theme name').fill('Currency risk');
  await page.getByRole('button', { name: 'Save' }).click();
  await expect(page.locator('.matrix')).toContainText('Currency risk');

  await page.getByRole('button', { name: 'Actions for Currency risk' }).click();
  await page.getByRole('menuitem', { name: 'Remove from matrix' }).click();
  await expect(page.getByText('Removed themes')).toBeVisible();
  await page.getByRole('button', { name: 'Restore' }).click();
  await expect(page.locator('.matrix')).toContainText('Currency risk');
});

test('the assistant applies chat instructions at a checkpoint', async ({ page }) => {
  await startBenchmark(page);
  await page.getByLabel('Message the assistant').fill('go');
  await page.getByRole('button', { name: 'Send' }).click();
  await expect(page.locator('.matrix')).toBeVisible({ timeout: 30_000 });
  await expect(page.locator('.msg.user')).toHaveText('go');
  await expect(page.locator('.msg.assistant').first()).toContainText('Scope locked.');
});

test('the method page and a phone-sized overview', async ({ page }) => {
  await page.goto('/#/method');
  await expect(page.getByRole('heading', { name: 'Risk taxonomy' })).toBeVisible();
  await expect(page.getByText('Provisional')).toBeVisible();
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto('/#/');
  await expect(page.getByRole('button', { name: 'Start a benchmark' })).toBeVisible();
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
  expect(overflow).toBe(0);
});

test('a running benchmark can be stopped and resumed', async ({ page }) => {
  await page.goto('/#/new');
  await page.getByPlaceholder('e.g. City Developments').fill('City Developments');
  await page.getByRole('button', { name: 'Start benchmark' }).click();
  await expect(page.locator('.progress-card')).toBeVisible();
  await page.screenshot({ path: 'test-results/running.png' });
  await page.getByRole('button', { name: 'Stop', exact: true }).first().click();
  await expect(page.getByText(/You stopped this benchmark while researching/)).toBeVisible({ timeout: 20_000 });
  await expect(page.getByText('Stopped. What finished is saved.')).toBeVisible();
  await expect(page.locator('.run-meta')).toContainText('Stopped');
  await page.waitForTimeout(1000);
  await page.screenshot({ path: 'test-results/stopped.png' });

  await page.getByRole('button', { name: 'Resume' }).first().click();
  await expect(page.getByRole('heading', { name: 'Peer candidates' })).toBeVisible({ timeout: 30_000 });
});

test('marking a document not available can be undone', async ({ page }) => {
  await startBenchmark(page);
  const client = page.locator('.doc-group', { has: page.getByRole('heading', { name: /CDL/ }) });
  await client.getByRole('button', { name: 'Mark n/a' }).first().click();
  await expect(client.getByText('Not available', { exact: true })).toBeVisible();
  await client.getByRole('button', { name: 'Undo' }).click();
  await expect(client.getByText('Checked', { exact: true })).toBeVisible();
  await expect(client.getByRole('button', { name: 'Undo' })).toHaveCount(0);
});
