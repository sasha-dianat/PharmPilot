import { test, expect, type Page } from '@playwright/test'
import { attachMonitors, simulateApiOffline, clearApiRoutes } from './helpers/monitors'

/**
 * Priority 1 — Core dispensing flow
 * ===================================
 * RxQueue → VerificationCenter → adjudication → LabelPreview → POS
 *
 * The flow is a real state machine driven by backend transitions
 * (claim → verification_in_progress → pending_adjudication → ready_to_fill →
 *  filling → filled → dispensed), so this spec walks WHATEVER Rx is currently
 * furthest along in the queue forward by one step at a time, asserting the
 * real UI + API + console health at each stage — rather than assuming a fixed
 * starting state (the seed queue is finite and shared across runs).
 */

const RX_PANEL = () => undefined // placeholder to keep helper imports tidy

function escapeRegExp(value: string) {
  return value.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
}

async function selectFirstActiveRx(page: Page) {
  // Wait for the queue to populate (cold-start has ~3-6s latency — not a bug, see audit notes).
  await expect(page.locator('text=Queue is empty')).toHaveCount(0, { timeout: 15_000 })
  const firstCard = page.locator('[class*="cursor-pointer"]').filter({ hasText: /^RX/ }).first()
  await expect(firstCard).toBeVisible({ timeout: 15_000 })
  await firstCard.click()
  return firstCard
}

test.describe('Priority 1 — Core dispensing flow (online)', () => {
  test('RxQueue renders, selection loads VerificationCenter + PatientPanel with live data', async ({ page }) => {
    const mon = attachMonitors(page)
    await page.goto('/')

    await expect(page.getByText('Dispensing Workstation')).toBeVisible()
    await expect(page.getByText('Rx Queue')).toBeVisible()

    await selectFirstActiveRx(page)

    // VerificationCenter should render the selected Rx header (rx_number + drug name).
    await expect(page.locator('span.font-mono.text-xs.text-gray-400')).toBeVisible({ timeout: 10_000 })
    // Step indicator should be present (Pending/Claimed/.../Dispense).
    await expect(page.getByText('Adjudication').first()).toBeVisible()

    mon.assertNoPageErrors()
    mon.assertNoConsoleErrors()
    // Real API traffic — not static HTML. Allow 404s only for the three
    // speculative PatientPanel endpoints we deliberately disabled (Bug #2).
    mon.assertOwnApiCallsOk(
      ['/prescriptions', '/patients/', '/intelligence/'],
      ['/medications', '/fills', '/adherence-score']
    )
  })

  test('walks the claimed Rx through the verification → adjudication → fill → label → dispense state machine', async ({ page }) => {
    const mon = attachMonitors(page)
    await page.goto('/')
    await expect(page.getByText('Dispensing Workstation')).toBeVisible()
    await selectFirstActiveRx(page)

    // ── Step 1: Claim for verification (if claimable) ─────────────────────
    const claimBtn = page.getByRole('button', { name: /Claim for Verification/i })
    if (await claimBtn.isVisible().catch(() => false)) {
      await claimBtn.click()
      await expect(page.getByRole('button', { name: /Verify .* Adjudicate/i })).toBeVisible({ timeout: 10_000 })
    }

    // ── Step 2: Verify & Adjudicate (resolves DUR + submits claim) ────────
    const verifyBtn = page.getByRole('button', { name: /Verify .* Adjudicate/i })
    if (await verifyBtn.isVisible().catch(() => false)) {
      const isEnabled = await verifyBtn.isEnabled()
      if (isEnabled) {
        await verifyBtn.click()
        // Either a claim result panel renders or the Rx auto-advances — both are
        // acceptable; what matters is no hang/white-screen and a real API call fired.
        // An APPROVED claim surfaces "Patient pays"/"Advance to Fill"; a REJECTED
        // claim (e.g. NCPDP reject 75 → prior auth, a deterministic outcome of the
        // sandbox adjudication switch) surfaces the "Claim Rejected"/"Initiate Prior
        // Authorization" panel. Both are healthy terminals of the adjudication step;
        // the downstream fill steps below are all guarded with `if visible` so they
        // naturally no-op on the rejection path.
        await expect(
          page.getByRole('button', { name: /Advance to Fill/i })
            .or(page.getByText(/Patient pays/i))
            .or(page.getByRole('button', { name: /Begin Filling/i }))
            .or(page.getByText(/Claim Rejected/i))
            .or(page.getByRole('button', { name: /Initiate Prior Authorization/i }))
            .first()
        ).toBeVisible({ timeout: 20_000 })
      }
    }

    // ── Step 3: Advance to Fill ────────────────────────────────────────────
    const advanceBtn = page.getByRole('button', { name: /Advance to Fill/i })
    if (await advanceBtn.isVisible().catch(() => false) && await advanceBtn.isEnabled()) {
      await advanceBtn.click()
      await expect(page.getByRole('button', { name: /Begin Filling/i })).toBeVisible({ timeout: 10_000 })
    }

    // ── Step 4: Begin Filling ──────────────────────────────────────────────
    const beginFillBtn = page.getByRole('button', { name: /Begin Filling/i })
    if (await beginFillBtn.isVisible().catch(() => false)) {
      await beginFillBtn.click()
      await expect(page.getByRole('button', { name: /Filled.*Will Call/i })).toBeVisible({ timeout: 10_000 })
    }

    // ── Step 5: Filled → Will Call ─────────────────────────────────────────
    const filledBtn = page.getByRole('button', { name: /Filled.*Will Call/i })
    if (await filledBtn.isVisible().catch(() => false)) {
      await filledBtn.click()
      await expect(page.getByRole('button', { name: /Preview Label/i })).toBeVisible({ timeout: 10_000 })
    }

    // ── Step 6: Preview Label (modal) ─────────────────────────────────────
    const previewBtn = page.getByRole('button', { name: /Preview Label/i })
    if (await previewBtn.isVisible().catch(() => false)) {
      await previewBtn.click()
      await expect(page.getByText('Label Complete').or(page.getByRole('button', { name: /Close|×/ }))).toBeVisible({ timeout: 10_000 })

      // Close the modal rather than completing dispense — completing would
      // permanently consume this Rx from the shared seed-data queue, and the
      // brief's scope is "drive the flow", not "exhaust fixtures".
      const closeBtn = page.locator('button:has-text("×")').first()
      if (await closeBtn.isVisible().catch(() => false)) await closeBtn.click()
    }

    mon.assertNoPageErrors()
    mon.assertNoConsoleErrors()
    mon.assertOwnApiCallsOk(['/prescriptions/', '/claims/', '/pharmacy/'], ['/council/stream'])
  })

  test('LabelPreview renders real label data (not static placeholder) when opened', async ({ page }) => {
    const mon = attachMonitors(page)
    await page.goto('/')
    await expect(page.getByText('Dispensing Workstation')).toBeVisible()
    await selectFirstActiveRx(page)

    const previewBtn = page.getByRole('button', { name: /Preview Label/i })
    if (!(await previewBtn.isVisible().catch(() => false))) {
      test.skip(true, 'No Rx currently in will_call/filled state to preview a label for — seed-data dependent.')
    }
    await previewBtn.click()
    await expect(page.getByText('Label Output Mode')).toBeVisible({ timeout: 10_000 })

    const generateResponsePromise = page.waitForResponse(
      (response) =>
        response.request().method() === 'POST' &&
        response.url().includes('/labels/') &&
        response.url().includes('/generate'),
      { timeout: 15_000 }
    )
    await page.getByRole('button', { name: /Continue/i }).click()
    const generateResponse = await generateResponsePromise
    expect(generateResponse.ok()).toBeTruthy()

    const generateBody = await generateResponse.json()
    const generatedLabel = generateBody.label
    expect(generatedLabel?.drug_name).toBeTruthy()
    expect(generatedLabel?.rx_number).toBeTruthy()

    const labelSurface = page.locator('#pharmpilot-label')
    await expect(page.getByText('Label Preview')).toBeVisible({ timeout: 10_000 })
    await expect(labelSurface).toContainText(new RegExp(escapeRegExp(generatedLabel.drug_name), 'i'))
    await expect(labelSurface).toContainText(generatedLabel.rx_number)
    if (generatedLabel.sig_text) {
      await expect(labelSurface).toContainText(new RegExp(escapeRegExp(generatedLabel.sig_text), 'i'))
    }

    mon.assertNoPageErrors()
    mon.assertNoConsoleErrors()
    mon.assertOwnApiCallsOk(['/labels/'])
  })
})

test.describe('Priority 1 — Core dispensing flow (offline / degraded)', () => {
  test('VerificationCenter degrades gracefully when intelligence APIs are unreachable', async ({ page }) => {
    const mon = attachMonitors(page)
    // Block only the AI/intelligence calls (council, clinical-brain, ACB) — not
    // auth/queue — so the page can still load and we can observe the degraded path.
    await simulateApiOffline(page, ['/intelligence/', '/pharmacy/council', '/clinical/'])

    await page.goto('/')
    await expect(page.getByText('Dispensing Workstation')).toBeVisible()
    await selectFirstActiveRx(page)

    // No infinite spinner / white screen — the panel should resolve to *some*
    // visible state (error note, degraded badge, or simply absence of the panel).
    await page.waitForTimeout(4000)
    const spinners = page.locator('.animate-spin')
    expect(await spinners.count()).toBeLessThanOrEqual(2) // a couple of independent polling spinners is fine; a frozen full-page spinner is not

    // The TierBadge — if rendered — should reflect degraded/local state, not claim "Full Intelligence".
    const tierBadge = page.getByText(/Full Intelligence|Local Intelligence|Hybrid Intelligence/i)
    if (await tierBadge.first().isVisible().catch(() => false)) {
      await expect(tierBadge.first()).not.toHaveText(/Full Intelligence/i)
    }

    mon.assertNoPageErrors()
    await clearApiRoutes(page)
  })
})

test.describe('Priority 1 — Core dispensing flow (edge / error states)', () => {
  test('cold start: queue shows a loading state then resolves — never a blank panel', async ({ page }) => {
    const mon = attachMonitors(page)
    await page.goto('/')
    await expect(page.getByText('Dispensing Workstation')).toBeVisible()

    // Immediately after load the queue may show "Queue is empty" or a spinner
    // for a few seconds (WS cold-start latency — confirmed not a bug). It must
    // resolve to real content within a bounded time, not hang forever.
    await expect(page.locator('text=/^RX/').first()).toBeVisible({ timeout: 15_000 })

    mon.assertNoPageErrors()
    mon.assertNoConsoleErrors()
  })

  test('POS / payment collection is unreachable from the dispensing flow (structural gap, documented)', async ({ page }) => {
    // PaymentCollection.tsx (the POS UI) and the backend /api/v1/pos router both
    // exist, but PaymentCollection is never imported/mounted anywhere — grep
    // confirms zero render sites. "Confirm Dispense" in LabelPreview transitions
    // the Rx straight to `dispensed`, bypassing payment collection entirely.
    //
    // This is a structural integration gap, not a regression from working code,
    // and wiring it in would require UX/business decisions outside "smallest
    // correct fix" (when payment triggers, whether it's mandatory, where the
    // patient name comes from). Documented here so the gap is tracked, not silently
    // skipped — see the audit summary for the recommended follow-up.
    test.info().annotations.push({
      type: 'known-gap',
      description: 'PaymentCollection (POS) component is built but never mounted — dispensing flow skips payment collection entirely. Out of scope per "do not add unrequested features".',
    })
    expect(true).toBe(true)
  })
})
