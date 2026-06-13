/**
 * TierBadge — the universal "which brain answered" chip
 * ======================================================
 * Required on every intelligent panel header (PART 4 of the Master Prompt).
 *   • Green  "Full Intelligence"  → online, cloud brain active
 *   • Amber  "Local Intelligence" → offline / degraded, local brain only
 *
 * Two ways to use it:
 *   1. Global, panel-agnostic (reads connectivity itself):
 *        <TierBadge />
 *   2. Per-response (reflects what a specific API call returned in its envelope):
 *        <TierBadge tier={resp.tier_used} degraded={resp.degraded}
 *                   optionsOffline={resp.options_offline} />
 *
 * When degraded, an optional tooltip lists the options that will return online.
 */
import { useIntelligenceTier, type Tier } from '../lib/useIntelligenceTier'

interface Props {
  /** Override with a specific response's tier; omit to read global connectivity. */
  tier?:           Tier
  degraded?:       boolean
  /** Sub-features that are unavailable right now (shown in the tooltip). */
  optionsOffline?: string[]
  /** Compact mode shows only the dot + short label. */
  compact?:        boolean
  className?:      string
}

export default function TierBadge({
  tier, degraded, optionsOffline, compact = false, className = '',
}: Props) {
  const global = useIntelligenceTier()

  const effectiveTier: Tier  = tier ?? global.tier
  const isDegraded: boolean  = degraded ?? global.degraded
  const isLocal = effectiveTier === 'local' || isDegraded

  const label = isLocal
    ? (effectiveTier === 'hybrid' ? 'Hybrid Intelligence' : 'Local Intelligence')
    : 'Full Intelligence'

  const colour = isLocal
    ? 'bg-amber-100 text-amber-800 border-amber-300'
    : 'bg-green-100 text-green-800 border-green-300'

  const dot = isLocal ? 'bg-amber-500' : 'bg-green-500'

  const offlineList = optionsOffline && optionsOffline.length > 0
    ? optionsOffline.join(', ')
    : null

  const tooltip = isLocal
    ? (offlineList
        ? `Local intelligence active. Returns online: ${offlineList}`
        : 'Running on the local brain — full options resume when connection is restored.')
    : 'Full cloud intelligence active — all options available.'

  return (
    <span
      title={tooltip}
      className={`inline-flex items-center gap-1.5 px-2 py-0.5 rounded-full border text-[11px] font-medium select-none ${colour} ${className}`}
    >
      <span className={`w-1.5 h-1.5 rounded-full ${dot} ${isLocal ? '' : 'animate-pulse'}`} />
      {compact ? (isLocal ? 'Local' : 'Full') : label}
    </span>
  )
}

/**
 * DegradedNote — the one-line "what returns online" banner (PART 4 #2).
 * Render under a panel when a response came back degraded.
 */
export function DegradedNote({ optionsOffline }: { optionsOffline?: string[] }) {
  if (!optionsOffline || optionsOffline.length === 0) return null
  return (
    <div className="text-[11px] text-amber-700 bg-amber-50 border border-amber-200 rounded px-2 py-1 mt-1">
      ⚡ Local mode — these options resume when online:{' '}
      <span className="font-medium">{optionsOffline.join(' · ')}</span>
    </div>
  )
}
