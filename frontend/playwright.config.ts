import { defineConfig } from '@playwright/test';

// The real frontend against the real API, with the fake model and fake documents
// (tests/e2e_server.py), so the browser tests run offline and cost nothing.
export default defineConfig({
  testDir: './tests',
  timeout: 60_000,
  fullyParallel: false,
  workers: 1,
  use: { baseURL: 'http://127.0.0.1:5174', channel: 'chrome', viewport: { width: 1480, height: 960 } },
  webServer: [
    { command: 'uv run python -m tests.e2e_server --port 8011 --delay 0.15', cwd: '..', port: 8011, reuseExistingServer: false },
    { command: 'npx vite --port 5174 --strictPort', env: { API_PROXY_TARGET: 'http://127.0.0.1:8011' }, port: 5174, reuseExistingServer: false },
  ],
});
