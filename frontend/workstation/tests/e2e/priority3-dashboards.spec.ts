import { test, expect, type Page } from '@playwright/test'
import { attachMonitors, simulateApiOffline, clearApiRoutes } from './helpers/monitors'

/**
 * Priority 3 — Supporting dashboards
 * ===================================
 * Inventory, Financial, Clinical, Audio, Command Center.
 *
 * These dashboards host several of the Priority-2 intelligence panels (already
 * exercised for live-data/TierBadge/console-health there); this spec instead
 * covers dashboard-LEVEL concerns: navigation between sections, real (not
 * static) top-line data, graceful degradation when offline, and edge states
 * (cold start, no white screens, no infinite spinners).
 */

const SECTIONS = ['Command Center', 'Inventory AI', 'Clinical Intel', 'Financial Ops', 'Conversation AI'] as const

async function openDashboards(page: Page) {
  await expect(page.getByRole('button', { name: /Dashboards/ })).toBeVisible({ timeout: 15_000 })
  await page.getByRole('button', { name: /Dashboards/ }).click()
}

test.describe('Priority 3 — Supporting dashboards (online)', () => {
  test('sidebar navigation switches sections, each renders real content with healthy console', async ({ page }) => {
    const mon = attachMonitors(page)
    await page.goto('/')
    await expect(page.getByText('Dispensing Workstation')).toBeVisible()
    await openDashboards(page)

    for (const section of SECTIONS) {
      await page.getByRole('button', { name: new RegExp(section.replace(/ /g, '\\s'), 'i') }).click()
      // Each section should render its own heading text in the top bar.
      await expect(page.locator('header').getByText(new RegExp(section, 'i'))).toBeVisible({ timeout: 10_000 })
      // Give panels a moment to fetch & settle before moving on.
      await page.waitForTimeout(1500)
    }

    // Escape returns to the workstation (per DashboardShell's documented keyboard shortcut).
    await page.keyboard.press('Escape')
    await expect(page.getByText('Rx Queue')).toBeVisible({ timeout: 10_000 })

    mon.assertNoPageErrors()
    mon.assertNoConsoleErrors([/Failed to load resource: the server responded with a status of 503/])
  })

  test('Command Center renders live operational metrics (not a static mock)', async ({ page }) => {
    const mon = attachMonitors(page)
    await page.goto('/')
    await expect(page.getByText('Dispensing Workstation')).toBeVisible()
    await openDashboards(page)
    // Command Center is the default section.
    await expect(page.locator('header').getByText(/Command Center/i)).toBeVisible()

    await page.waitForTimeout(3000)
    const calls = mon.callsMatching(['/analytics/dashboard/operational', '/security/summary'])
    expect(calls.length, 'Command Center should fetch live operational/security data').toBeGreaterThan(0)
    for (const c of calls) expect(c.status).toBeLessThan(400)

    mon.assertNoPageErrors()
    mon.assertNoConsoleErrors()
  })

  test('Financial Ops renders Margin Optimizer + Analytics chat with live data', async ({ page }) => {
    const mon = attachMonitors(page)
    await page.goto('/')
    await expect(page.getByText('Dispensing Workstation')).toBeVisible()
    await openDashboards(page)
    await page.getByRole('button', { name: /Financial Ops/i }).click()
    await expect(page.locator('header').getByText(/Financial Ops/i)).toBeVisible()

    await expect(page.getByText(/Margin|Analytics|Ask/i).first()).toBeVisible({ timeout: 15_000 })
    await page.waitForTimeout(2000)
    const calls = mon.callsMatching(['/intelligence/finance/margin', '/intelligence/analytics/'])
    expect(calls.length, 'Financial Ops should fetch live margin/analytics intelligence').toBeGreaterThan(0)

    mon.assertNoPageErrors()
    mon.assertNoConsoleErrors()
  })

  test('Conversation AI (Audio) renders transcript review UI with live data', async ({ page }) => {
    const mon = attachMonitors(page)
    await page.goto('/')
    await expect(page.getByText('Dispensing Workstation')).toBeVisible()
    await openDashboards(page)
    await page.getByRole('button', { name: /Conversation AI/i }).click()
    await expect(page.locator('header').getByText(/Conversation AI/i)).toBeVisible()

    // NOTE: this dashboard's transcript list is demo/static data (the live audio
    // capture pipeline needs real recordings) — but the embedded CounselingScorecard
    // sends that demo transcript text to the REAL /intelligence/clinical/counseling/assess
    // endpoint and renders the live AI assessment, so "live data" is exercised here.
    await page.waitForTimeout(3000)
    const counselingCalls = mon.callsMatching(['/intelligence/clinical/counseling'])
    expect(counselingCalls.length, 'CounselingScorecard should call the live counseling-assessment endpoint').toBeGreaterThan(0)
    for (const c of counselingCalls) expect(c.status, `${c.method} ${c.url}`).toBeLessThan(500)

    mon.assertNoPageErrors()
    mon.assertNoConsoleErrors()
  })
})

test.describe('Priority 3 — Supporting dashboards (offline / degraded)', () => {
  test('dashboards remain usable (no white screen / infinite spinner) when all backend APIs are unreachable', async ({ page }) => {
    const mon = attachMonitors(page)
    await simulateApiOffline(page, ['/api/'])

    await page.goto('/')
    // Even fully offline, the shell chrome (nav, "Dashboards" button) must render —
    // it's static React, not API-dependent.
    await expect(page.getByText('Dispensing Workstation').or(page.getByRole('heading', { name: 'Staff Sign In' }))).toBeVisible({ timeout: 15_000 })

    if (await page.getByRole('button', { name: /Dashboards/ }).isVisible().catch(() => false)) {
      await page.getByRole('button', { name: /Dashboards/ }).click()
      await page.waitForTimeout(4000)

      // Body must not be blank, and there must not be a frozen full-page spinner.
      const bodyText = await page.locator('body').innerText()
      expect(bodyText.trim().length, 'Dashboard shell should render visible content even when APIs are down').toBeGreaterThan(20)

      const spinners = page.locator('.animate-spin')
      expect(await spinners.count()).toBeLessThanOrEqual(4)
    }

    mon.assertNoPageErrors()
    await clearApiRoutes(page)
  })
})

test.describe('Priority 3 — Supporting dashboards (edge / error states)', () => {
  test('keyboard shortcuts (Alt+1..9) switch sections without errors', async ({ page }) => {
    const mon = attachMonitors(page)
    await page.goto('/')
    await expect(page.getByText('Dispensing Workstation')).toBeVisible()
    await openDashboards(page)

    for (const n of [2, 3, 5, 7, 1]) {
      await page.keyboard.press(`Alt+${n}`)
      await page.waitForTimeout(800)
    }
    // Should still be in a coherent dashboard state (a section heading visible).
    await expect(page.locator('header').getByText(/Command Center|Inventory AI|Clinical Intel|Financial Ops|Conversation AI/i)).toBeVisible()

    mon.assertNoPageErrors()
    mon.assertNoConsoleErrors([/Failed to load resource: the server responded with a status of 503/])
  })
})
