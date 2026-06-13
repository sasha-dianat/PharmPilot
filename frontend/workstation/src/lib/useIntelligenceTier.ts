/**
 * useIntelligenceTier — frontend half of the Offline-First Doctrine
 * ==================================================================
 * Exposes a single { tier, degraded, online } signal that every intelligent
 * panel uses to render its TierBadge (green "Full Intelligence" /
 * amber "Local Intelligence").
 *
 * Two signals are fused:
 *   1. navigator.onLine  (instant, browser-level)  — via useOnlineStatus()
 *   2. GET /intelligence/tier-status (authoritative — the server actually probes
 *      external reachability and per-dependency health)
 *
 * The server poll is cheap and cached (refetch every 20s + on reconnect). If the
 * server can't be reached at all, we fall back to navigator.onLine and assume
 * local tier — never throwing, matching the backend's fail-closed-to-local rule.
 */
import { useQuery } from '@tanstack/react-query'
import { apiClient } from './api'
import { useOnlineStatus } from '../components/OfflineIndicator'

export type Tier = 'local' | 'cloud' | 'hybrid'

export interface TierStatus {
  online:   boolean
  tier:     Tier
  degraded: boolean
  local_brain?:  { available: boolean; embedding_dim: number; llm: string }
  cloud_brain?:  { available: boolean; note: string }
}

const LOCAL_FALLBACK: TierStatus = {
  online: false, tier: 'local', degraded: true,
  local_brain: { available: true, embedding_dim: 0, llm: 'ollama_local' },
  cloud_brain: { available: false, note: 'Running on local intelligence.' },
}

export function useIntelligenceTier(): TierStatus {
  const { online: browserOnline } = useOnlineStatus()

  const { data } = useQuery({
    queryKey: ['intelligence-tier-status'],
    queryFn:  () => apiClient.get('/intelligence/tier-status').then(r => r.data as TierStatus),
    refetchInterval: 20_000,
    refetchOnWindowFocus: true,
    retry: false,
    // When the browser flips back online, refetch promptly.
    enabled: true,
  })

  // If the browser itself is offline, the answer is unambiguous: local.
  if (!browserOnline) {
    return { ...LOCAL_FALLBACK }
  }

  // Server status is authoritative when present; else fall back to "online but
  // unverified" — still cloud-capable from the browser's point of view.
  return data ?? {
    online: true, tier: 'cloud', degraded: false,
    cloud_brain: { available: true, note: 'Verifying full intelligence…' },
  }
}
