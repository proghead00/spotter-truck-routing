import { defineConfig, devices } from '@playwright/test'

// End-to-end: the production build served by Django/gunicorn, exactly as deployed, with the
// deterministic fake providers so runs never depend on third-party APIs.
const PORT = 8765
const PYTHON = process.env.PYTHON ?? '../.venv/bin/python'

export default defineConfig({
  testDir: './e2e',
  timeout: 30_000,
  fullyParallel: true,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 1 : 0,
  reporter: process.env.CI ? 'github' : 'list',
  use: {
    baseURL: `http://127.0.0.1:${PORT}`,
    trace: 'retain-on-failure',
  },
  projects: [
    { name: 'desktop', use: { ...devices['Desktop Chrome'], viewport: { width: 1440, height: 900 } } },
    { name: 'mobile', use: { ...devices['Pixel 7'] } },
  ],
  webServer: {
    command: `npm run build && cd ../backend && ${PYTHON} -m gunicorn config.wsgi -b 127.0.0.1:${PORT} -w 2`,
    url: `http://127.0.0.1:${PORT}/api/health`,
    reuseExistingServer: !process.env.CI,
    timeout: 120_000,
    env: { PROVIDERS: 'fake', DJANGO_SECRET_KEY: 'e2e-only', DJANGO_DEBUG: '0', PLAN_RATE_LIMIT: '1000/min' },
  },
})
