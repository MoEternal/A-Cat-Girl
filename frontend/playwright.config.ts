import { defineConfig } from '@playwright/test'

export default defineConfig({
  testDir: './tests',
  outputDir: './test-results',
  reporter: 'line',
  use: {
    baseURL: process.env.PLAYWRIGHT_BASE_URL ?? 'http://127.0.0.1:8732',
    channel: 'msedge',
    trace: 'retain-on-failure',
  },
  webServer: {
    command: 'uv run --project .. python ../scripts/playwright_server.py',
    url: 'http://127.0.0.1:8732/health',
    reuseExistingServer: false,
    timeout: 60_000,
  },
})
