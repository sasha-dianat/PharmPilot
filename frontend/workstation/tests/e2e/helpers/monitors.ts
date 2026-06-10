import type { Page, Request, Response } from '@playwright/test'

/**
 * Shared page-health monitors for the browser audit.
 *
 * Every spec should call `attachMonitors(page)` immediately after obtaining the
 * page (before navigation) and then assert via the returned collectors at the
 * end of the test:
 *
 *   const mon = attachMonitors(page)
 *   await page.goto('/')
 *   ...
 *   mon.assertNoConsoleErrors()
 *   mon.assertNoPageErrors()
 *   mon.assertOwnApiCallsOk(['/intelligence/'])
 *
 * This implements the "fail the test on console errors / uncaught exceptions /
 * failed own-API requests" rule from the audit brief.
 */

export interface ApiCallRecord {
  url: string
  method: string
  status?: number
  ok?: boolean
  failure?: string | null
}

export interface PageMonitors {
  consoleErrors: string[]
  consoleWarnings: string[]
  pageErrors: string[]
  requestFailures: { url: string; failure: string }[]
  apiCalls: ApiCallRecord[]
  /** Throws if any console.error()/error-level log was captured. */
  assertNoConsoleErrors: (allow?: RegExp[]) => void
  /** Throws if any uncaught exception / unhandled rejection was captured. */
  assertNoPageErrors: () => void
  /** Throws if any request matching `urlSubstrings` failed at the network layer or returned 4xx/5xx. */
  assertOwnApiCallsOk: (urlSubstrings: string[], allow4xxFor?: string[]) => void
  /** Returns the API calls whose URL contains any of the given substrings. */
  callsMatching: (urlSubstrings: string[]) => ApiCallRecord[]
  reset: () => void
}

const KNOWN_BENIGN_CONSOLE_PATTERNS: RegExp[] = [
  // React Query / React 19 dev-mode noise that isn't a real bug:
  /Download the React DevTools/i,
  /\[vite\] connect/i,
  /\[vite\] connecting/i,
]

export function attachMonitors(page: Page): PageMonitors {
  const consoleErrors: string[] = []
  const consoleWarnings: string[] = []
  const pageErrors: string[] = []
  const requestFailures: { url: string; failure: string }[] = []
  const apiCalls: ApiCallRecord[] = []

  page.on('console', (msg) => {
    const text = msg.text()
    if (KNOWN_BENIGN_CONSOLE_PATTERNS.some((re) => re.test(text))) return
    if (msg.type() === 'error') consoleErrors.push(text)
    if (msg.type() === 'warning') consoleWarnings.push(text)
  })

  page.on('pageerror', (err) => {
    pageErrors.push(err.message || String(err))
  })

  page.on('requestfailed', (req: Request) => {
    requestFailures.push({ url: req.url(), failure: req.failure()?.errorText ?? 'unknown' })
  })

  page.on('response', (res: Response) => {
    const req = res.request()
    const url = req.url()
    if (url.includes('/api/')) {
      apiCalls.push({
        url,
        method: req.method(),
        status: res.status(),
        ok: res.ok(),
        failure: req.failure()?.errorText ?? null,
      })
    }
  })

  function assertNoConsoleErrors(allow: RegExp[] = []) {
    const real = consoleErrors.filter((e) => !allow.some((re) => re.test(e)))
    if (real.length) {
      throw new Error(`Console errors detected (${real.length}):\n` + real.map((e) => `  • ${e}`).join('\n'))
    }
  }

  function assertNoPageErrors() {
    if (pageErrors.length) {
      throw new Error(`Uncaught page errors detected (${pageErrors.length}):\n` + pageErrors.map((e) => `  • ${e}`).join('\n'))
    }
  }

  function callsMatching(urlSubstrings: string[]) {
    return apiCalls.filter((c) => urlSubstrings.some((s) => c.url.includes(s)))
  }

  function assertOwnApiCallsOk(urlSubstrings: string[], allow4xxFor: string[] = []) {
    const matches = callsMatching(urlSubstrings)
    const bad = matches.filter((c) => {
      if (c.failure) return true
      if (c.status === undefined) return false
      if (c.status >= 200 && c.status < 400) return false
      // Allow specific endpoints to 4xx by design (e.g. "no data yet" 404s the UI handles).
      if (c.status >= 400 && c.status < 500 && allow4xxFor.some((s) => c.url.includes(s))) return false
      return c.status >= 400
    })
    if (bad.length) {
      throw new Error(
        `Own-API calls failed (${bad.length}):\n` +
          bad.map((c) => `  • [${c.status ?? 'NETWORK_ERR ' + c.failure}] ${c.method} ${c.url}`).join('\n')
      )
    }
  }

  function reset() {
    consoleErrors.length = 0
    consoleWarnings.length = 0
    pageErrors.length = 0
    requestFailures.length = 0
    apiCalls.length = 0
  }

  return {
    consoleErrors,
    consoleWarnings,
    pageErrors,
    requestFailures,
    apiCalls,
    assertNoConsoleErrors,
    assertNoPageErrors,
    assertOwnApiCallsOk,
    callsMatching,
    reset,
  }
}

/**
 * Simulate the API being unreachable for a set of URL patterns — used for the
 * "offline / degraded" leg of the per-service protocol (per the brief's
 * suggestion to use page.route(...) request-blocking instead of restarting
 * the API with PHARMPILOT_FORCE_OFFLINE=1, which would affect every spec).
 */
export async function simulateApiOffline(page: Page, urlSubstrings: string[]) {
  await page.route('**/api/**', async (route) => {
    const url = route.request().url()
    if (urlSubstrings.some((s) => url.includes(s))) {
      await route.abort('connectionrefused')
    } else {
      await route.continue()
    }
  })
}

export async function clearApiRoutes(page: Page) {
  await page.unroute('**/api/**')
}
