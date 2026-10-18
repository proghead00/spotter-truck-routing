import { expect, test, type Page } from '@playwright/test'

const errors: string[] = []

test.beforeEach(async ({ page }) => {
  errors.length = 0
  // Basemap tiles are third-party; keep the suite offline and deterministic.
  await page.route(/arcgisonline\.com/, (route) => route.fulfill({ status: 204 }))
  page.on('pageerror', (e) => errors.push(e.message))
  page.on('console', (m) => m.type() === 'error' && errors.push(m.text()))
  await page.goto('/')
})

test.afterEach(() => {
  expect(errors).toEqual([])
})

async function planTrip(page: Page, destination = 'St. Louis') {
  await page.getByLabel('Destination', { exact: true }).fill(destination)
  await page.getByRole('button', { name: 'Find safest route' }).click()
  await expect(page.locator('.route-card')).toHaveCount(3)
}

test('plans three routes and recommends the one that avoids the storm', async ({ page }) => {
  await expect(page).toHaveTitle('Weather-Aware Truck Routing')
  await expect(page.getByText('Enter a trip to compare three routes')).toBeVisible()

  await planTrip(page)

  const cards = page.locator('.route-card')
  await expect(cards.first()).toContainText('Recommended')
  await expect(cards.first()).toHaveAttribute('aria-pressed', 'true')
  // The fake storm sits on the direct line, so the recommendation is the detour that never enters No Travel.
  await expect(page.locator('.recommendation')).toContainText(/Route B is the safest choice\. Fewest No Travel miles: 0 mi vs 50 mi/)
  for (const card of await cards.all()) {
    await expect(card).toContainText('Distance')
    await expect(card).toContainText('Travel time')
    await expect(card).toContainText('Arrive')
    await expect(card.locator('.strip span').first()).toBeVisible()
  }

  // Map: heatmap image, route summary labels, origin/destination and a truck per route.
  await expect(page.locator('img.heatmap-layer')).toHaveAttribute('src', /^data:image\/png/)
  await expect(page.locator('.route-label')).toHaveCount(3)
  await expect(page.locator('.route-label.selected')).toContainText('recommended')
  await expect(page.locator('.truck-marker')).toHaveCount(3)
  await expect(page.locator('canvas.leaflet-zoom-animated')).toBeVisible()

  // Checkpoint table for the selected route, every 25 mi with ETAs and risk.
  await page.getByText(/Route B checkpoints/).click()
  const rows = page.locator('.checkpoints tbody tr')
  expect(await rows.count()).toBeGreaterThan(10)
  await expect(rows.first()).toContainText('Low')
  await expect(page.locator('.checkpoints')).toContainText('High')
})

test('selecting a route card switches the highlighted route', async ({ page }) => {
  await planTrip(page)
  const routeA = page.locator('.route-card[data-route="A"]')
  await routeA.click()
  await expect(routeA).toHaveAttribute('aria-pressed', 'true')
  await expect(page.locator('.route-label.selected')).toContainText('A ·')
  await expect(page.getByText(/Route A checkpoints/)).toBeVisible()
  await expect(routeA).toContainText(/Severe\s*\d+ mi/)
})

test('forecast slider moves the heatmap and the trucks through 48 hours', async ({ page }) => {
  await planTrip(page)
  const heat = page.locator('img.heatmap-layer')
  const first = await heat.getAttribute('src')
  const truck = page.locator('.truck-marker.selected')
  const start = await truck.evaluate((el) => el.style.transform)

  await page.getByLabel('Forecast hour').fill('3')
  await expect(page.locator('.timeline-time')).toContainText('+3 h after departure')
  await expect(heat).not.toHaveAttribute('src', first!)
  await expect.poll(() => truck.evaluate((el) => el.style.transform)).not.toBe(start)

  await page.getByLabel('Forecast hour').fill('48')
  await expect(page.locator('.timeline-time')).toContainText('+48 h')

  // Layers: wind uses mph thresholds, and the heatmap can be hidden.
  await page.locator('.timeline label', { hasText: 'Wind' }).click()
  await expect(page.locator('.legend-unit')).toHaveText('mph')
  await page.getByRole('checkbox', { name: 'Heatmap' }).uncheck()
  await expect(heat).toHaveCount(0)
})

test('play button animates the forecast', async ({ page }) => {
  await planTrip(page)
  await page.getByRole('button', { name: 'Play forecast' }).click()
  await expect(page.locator('.timeline-time')).toContainText(/\+[2-9] h/, { timeout: 5000 })
  await page.getByRole('button', { name: 'Pause forecast' }).click()
})

test('long trips include hours-of-service stops', async ({ page }) => {
  await planTrip(page, 'Denver')
  await expect(page.locator('.route-card').first()).toContainText(/1 break.*1 overnight rest/)
  await expect(page.locator('.stop-marker')).toHaveCount(2)
})

test('suggests places while typing', async ({ page }) => {
  await page.getByLabel('Destination', { exact: true }).fill('Kan')
  await expect(page.locator('datalist option[value="Kansas City, Missouri, United States"]')).toHaveCount(1)
})

test('shows validation errors next to the field', async ({ page }) => {
  await page.getByLabel('Origin', { exact: true }).fill('Atlantis')
  await page.getByRole('button', { name: 'Find safest route' }).click()
  await expect(page.getByText('Could not find "Atlantis".')).toBeVisible()
  await expect(page.getByLabel('Origin', { exact: true })).toHaveAttribute('aria-invalid', 'true')
  errors.length = 0 // the browser logs the 400 itself
})

test('shows upstream failures', async ({ page }) => {
  await page.route('**/api/plan', (route) =>
    route.fulfill({ status: 502, json: { detail: 'Upstream data provider failed: api.open-meteo.com is rate-limiting' } }),
  )
  await page.getByRole('button', { name: 'Find safest route' }).click()
  await expect(page.getByRole('alert')).toContainText('rate-limiting')
  errors.length = 0 // the browser logs the 502 itself
})

for (const scheme of ['light', 'dark'] as const) {
  test(`renders in ${scheme} mode with reduced motion`, async ({ page }, info) => {
    await page.emulateMedia({ colorScheme: scheme, reducedMotion: 'reduce' })
    await planTrip(page)
    const bg = await page.evaluate(() => getComputedStyle(document.querySelector('.panel')!).backgroundColor)
    expect(bg).toBe(scheme === 'dark' ? 'rgb(20, 29, 32)' : 'rgb(255, 255, 255)')
    const overflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth)
    expect(overflow).toBeLessThanOrEqual(0)
    await page.screenshot({ path: info.outputPath(`${scheme}.png`) })
  })
}
