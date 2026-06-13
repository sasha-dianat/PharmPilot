/**
 * OfflineIndicator — Phase 33
 * ============================
 * Fixed amber bar at the top of the screen when internet connection is lost.
 * Disappears automatically when the connection is restored.
 *
 * Also exports:
 *   useOnlineStatus()  — hook returning { online, lastOnlineAt }
 *   useOfflineQueue()  — hook for queuing mutations to retry when back online
 */
import { useState, useEffect, useCallback, useRef } from 'react'

// ─── useOnlineStatus ────────────────────────────────────────────────────────

export interface OnlineStatus {
  online:       boolean
  lastOnlineAt: Date | null
  offlineSince: Date | null
}

export function useOnlineStatus(): OnlineStatus {
  const [status, setStatus] = useState<OnlineStatus>({
    online:       navigator.onLine,
    lastOnlineAt: navigator.onLine ? new Date() : null,
    offlineSince: navigator.onLine ? null : new Date(),
  })

  useEffect(() => {
    const handleOnline = () => setStatus({
      online: true, lastOnlineAt: new Date(), offlineSince: null,
    })
    const handleOffline = () => setStatus(s => ({
      online: false, lastOnlineAt: s.lastOnlineAt, offlineSince: new Date(),
    }))

    window.addEventListener('online',  handleOnline)
    window.addEventListener('offline', handleOffline)
    return () => {
      window.removeEventListener('online',  handleOnline)
      window.removeEventListener('offline', handleOffline)
    }
  }, [])

  return status
}

// ─── useOfflineQueue ─────────────────────────────────────────────────────────

interface QueuedAction {
  id:          string
  description: string
  fn:          () => Promise<unknown>
  queuedAt:    Date
}

let _queue: QueuedAction[] = []
let _listeners: Set<() => void> = new Set()

function notifyListeners() {
  _listeners.forEach(fn => fn())
}

export function useOfflineQueue() {
  const [queue, setQueue] = useState<QueuedAction[]>([..._queue])
  const { online } = useOnlineStatus()

  // Subscribe to queue changes
  useEffect(() => {
    const update = () => setQueue([..._queue])
    _listeners.add(update)
    return () => { _listeners.delete(update) }
  }, [])

  // Flush queue when back online
  useEffect(() => {
    if (!online || _queue.length === 0) return
    const toFlush = [..._queue]
    _queue = []
    notifyListeners()
    toFlush.forEach(async (action) => {
      try { await action.fn() }
      catch (e) { console.warn('[OfflineQueue] Flush failed for:', action.description, e) }
    })
  }, [online])

  const enqueue = useCallback((description: string, fn: () => Promise<unknown>) => {
    const action: QueuedAction = {
      id:          Math.random().toString(36).slice(2),
      description,
      fn,
      queuedAt:    new Date(),
    }
    _queue.push(action)
    notifyListeners()
  }, [])

  const clearQueue = useCallback(() => {
    _queue = []
    notifyListeners()
  }, [])

  return { queue, enqueue, clearQueue, pendingCount: queue.length }
}

// ─── OfflineIndicator component ──────────────────────────────────────────────

export default function OfflineIndicator() {
  const { online, offlineSince } = useOnlineStatus()
  const { pendingCount }         = useOfflineQueue()
  const [elapsed, setElapsed]    = useState(0)
  const timerRef = useRef<ReturnType<typeof setInterval> | null>(null)

  useEffect(() => {
    if (!online && offlineSince) {
      timerRef.current = setInterval(() => {
        setElapsed(Math.floor((Date.now() - offlineSince.getTime()) / 1000))
      }, 1000)
    } else {
      setElapsed(0)
      if (timerRef.current) clearInterval(timerRef.current)
    }
    return () => { if (timerRef.current) clearInterval(timerRef.current) }
  }, [online, offlineSince])

  if (online) return null

  const mins = Math.floor(elapsed / 60)
  const secs = elapsed % 60
  const durationStr = mins > 0 ? `${mins}m ${secs}s` : `${secs}s`

  return (
    <div className="pp-offline-bar" role="alert" aria-live="assertive">
      <span className="text-lg">📡</span>
      <span>
        <strong>No internet connection</strong>
        {elapsed > 0 && <span className="opacity-75"> · offline for {durationStr}</span>}
      </span>
      {pendingCount > 0 && (
        <span className="bg-amber-600 text-amber-100 text-xs px-2 py-0.5 rounded-full">
          {pendingCount} action{pendingCount !== 1 ? 's' : ''} queued
        </span>
      )}
      <span className="text-xs opacity-70">
        · Local cache active · Changes will sync when connection restores
      </span>
    </div>
  )
}
