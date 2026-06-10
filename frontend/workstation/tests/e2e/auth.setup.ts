import { test as setup, expect } from '@playwright/test'
import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const __dirname = path.dirname(fileURLToPath(import.meta.url))

/**
 * Auth setup — runs once before the audit specs.
 *
 * Logs in through the REAL UI as `pharmacist / Pharmacist2024!` (exercising the
 * actual login form + /api/v1/auth/login call), waits for the workstation shell
 * to render, then persists localStorage (access_token, refresh_token, user_role,
 * pharmacy_id, staff_id) as Playwright storage state. Every other spec loads
 * this state and starts already authenticated — matching the "persist the
 * session so each test starts logged in" requirement.
 */
const AUTH_DIR = path.join(__dirname, '.auth')
const AUTH_FILE = path.join(AUTH_DIR, 'pharmacist.json')

setup('authenticate as pharmacist', async ({ page }) => {
  fs.mkdirSync(AUTH_DIR, { recursive: true })

  await page.goto('/')

  await expect(page.getByRole('heading', { name: 'Staff Sign In' })).toBeVisible()

  await page.getByPlaceholder('Enter your username').fill('pharmacist')
  await page.getByPlaceholder('Enter your password').fill('Pharmacist2024!')
  await page.getByRole('button', { name: /Sign In/i }).click()

  // Wait for the workstation shell (RxQueue / "Live" badge / Dashboards button) to render.
  await expect(page.getByText('Dispensing Workstation')).toBeVisible({ timeout: 20_000 })
  await expect(page.getByRole('button', { name: /Dashboards/ })).toBeVisible()

  // Sanity: confirm the JWT actually landed in localStorage (not just UI state).
  const token = await page.evaluate(() => localStorage.getItem('access_token'))
  expect(token, 'access_token should be persisted to localStorage after login').toBeTruthy()

  const role = await page.evaluate(() => localStorage.getItem('user_role'))
  expect(role).toBe('pharmacist')

  await page.context().storageState({ path: AUTH_FILE })
})
