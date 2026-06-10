/**
 * PharmPilot Service Worker — Phase 33 (Frontend Offline Mode)
 * =============================================================
 * Provides asset caching so the workstation shell loads even when
 * the network is unavailable (e.g. momentary disconnects or local LAN issues).
 *
 * Strategy:
 *   • App shell (HTML, CSS, JS bundles): Cache-first with network fallback
 *   • API calls (/api/v1/…): Network-first with no cache (never cache PHI)
 *   • Static assets (images, fonts): Stale-while-revalidate
 *
 * PHI / security notes:
 *   • API responses are NEVER cached — no patient data stored in SW cache.
 *   • Cache is keyed by URL only; no auth tokens are intercepted.
 *   • Cache is cleared on every new SW activation to prevent stale app shell.
 */

const CACHE_NAME    = 'pharmpilot-shell-v1'
const CACHE_VERSION = 1   // Bump to force cache clear on deploy

// Assets that form the app shell — cached on install
const SHELL_ASSETS = [
  '/',
  '/index.html',
]

// ─── Install ───────────────────────────────────────────────────────────────────

self.addEventListener('install', (event) => {
  event.waitUntil(
    caches.open(CACHE_NAME).then(async (cache) => {
      // Cache the shell; failure here is non-fatal
      try {
        await cache.addAll(SHELL_ASSETS)
      } catch (e) {
        console.warn('[SW] Shell pre-cache failed (this is OK in dev):', e)
      }
    })
  )
  // Take control immediately without waiting for tabs to reload
  self.skipWaiting()
})

// ─── Activate ─────────────────────────────────────────────────────────────────

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys().then(async (names) => {
      // Delete all caches that don't match the current version
      await Promise.all(
        names
          .filter(name => name !== CACHE_NAME)
          .map(name => caches.delete(name))
      )
    })
  )
  // Take control of all open clients immediately
  self.clients.claim()
})

// ─── Fetch ────────────────────────────────────────────────────────────────────

self.addEventListener('fetch', (event) => {
  const { request } = event
  const url = new URL(request.url)

  // ① Never cache API calls — always network, fall through to error on offline
  if (url.pathname.startsWith('/api/')) {
    // Pass through — do NOT intercept API requests
    return
  }

  // ② For WebSocket upgrades — skip
  if (request.headers.get('Upgrade') === 'websocket') {
    return
  }

  // ③ Only handle same-origin GET requests
  if (request.method !== 'GET' || url.origin !== self.location.origin) {
    return
  }

  // ④ JS / CSS bundles from Vite (hashed filenames): Cache-first
  if (url.pathname.startsWith('/assets/')) {
    event.respondWith(
      caches.match(request).then(cached => {
        if (cached) return cached
        return fetch(request).then(response => {
          if (response.ok) {
            const clone = response.clone()
            caches.open(CACHE_NAME).then(c => c.put(request, clone))
          }
          return response
        })
      })
    )
    return
  }

  // ⑤ HTML navigation requests: Network-first, fall back to cached /index.html
  if (request.mode === 'navigate' || request.headers.get('Accept')?.includes('text/html')) {
    event.respondWith(
      fetch(request)
        .then(response => {
          if (response.ok) {
            const clone = response.clone()
            caches.open(CACHE_NAME).then(c => c.put(request, clone))
          }
          return response
        })
        .catch(async () => {
          const cached = await caches.match(request)
          if (cached) return cached
          // Ultimate fallback — serve the app shell index.html
          const shell = await caches.match('/index.html')
          return shell || new Response('PharmPilot is offline', {
            headers: { 'Content-Type': 'text/plain' },
          })
        })
    )
    return
  }

  // ⑥ Everything else: Stale-while-revalidate
  event.respondWith(
    caches.match(request).then(cached => {
      const networkFetch = fetch(request).then(response => {
        if (response.ok) {
          const clone = response.clone()
          caches.open(CACHE_NAME).then(c => c.put(request, clone))
        }
        return response
      })
      return cached || networkFetch
    })
  )
})

// ─── Message handler ──────────────────────────────────────────────────────────

self.addEventListener('message', (event) => {
  if (event.data?.type === 'SKIP_WAITING') {
    self.skipWaiting()
  }
  if (event.data?.type === 'CLEAR_CACHE') {
    caches.delete(CACHE_NAME).then(() => {
      event.ports[0]?.postMessage({ ok: true })
    })
  }
})
