/**
 * Mission Control — severity token map.
 * Single source of truth bridging clinical severity strings to the design
 * tokens declared in index.css (@theme). Every AI/clinical surface resolves
 * colours through here so the palette never drifts per-component.
 */
export type Severity =
  | 'blocker' | 'caution' | 'warning' | 'intel' | 'safe' | 'counsel' | 'dur' | 'neutral'

interface SevToken {
  /** hex for SVG fills/strokes (charts, rings, sparklines) */
  hex: string
  /** tailwind text-* utility (tokens registered via @theme) */
  text: string
  /** translucent surface tint for chips/cards */
  bg: string
  /** translucent border */
  border: string
  /** glow colour for the one-shot entry pulse (--mc-glow) */
  glow: string
}

export const SEVERITY: Record<Severity, SevToken> = {
  blocker: { hex: '#ef4444', text: 'text-[#ef4444]', bg: 'bg-[#ef4444]/10', border: 'border-[#ef4444]/30', glow: 'rgba(239,68,68,0.45)' },
  caution: { hex: '#fb923c', text: 'text-[#fb923c]', bg: 'bg-[#fb923c]/10', border: 'border-[#fb923c]/25', glow: 'rgba(251,146,60,0.40)' },
  warning: { hex: '#fbbf24', text: 'text-[#fbbf24]', bg: 'bg-[#fbbf24]/10', border: 'border-[#fbbf24]/25', glow: 'rgba(251,191,36,0.40)' },
  intel:   { hex: '#60a5fa', text: 'text-[#60a5fa]', bg: 'bg-[#60a5fa]/10', border: 'border-[#60a5fa]/25', glow: 'rgba(96,165,250,0.40)' },
  safe:    { hex: '#34d399', text: 'text-[#34d399]', bg: 'bg-[#34d399]/10', border: 'border-[#34d399]/25', glow: 'rgba(52,211,153,0.40)' },
  counsel: { hex: '#a78bfa', text: 'text-[#a78bfa]', bg: 'bg-[#a78bfa]/10', border: 'border-[#a78bfa]/25', glow: 'rgba(167,139,250,0.40)' },
  dur:     { hex: '#fb7185', text: 'text-[#fb7185]', bg: 'bg-[#fb7185]/10', border: 'border-[#fb7185]/30', glow: 'rgba(251,113,133,0.45)' },
  neutral: { hex: '#8a97ab', text: 'text-[#8a97ab]', bg: 'bg-white/5',     border: 'border-white/10',     glow: 'transparent' },
}

/** Map backend severity vocab → design severity. */
export function toSeverity(raw: string | null | undefined): Severity {
  switch ((raw ?? '').toLowerCase()) {
    case 'critical': case 'blocker': case 'high': case 'severe': return 'blocker'
    case 'caution': case 'moderate': return 'caution'
    case 'warning': case 'warn': case 'low': return 'warning'
    case 'safe': case 'ok': case 'resolved': case 'normal': return 'safe'
    case 'counsel': case 'counselling': case 'counseling': return 'counsel'
    case 'dur': return 'dur'
    case 'intel': case 'info': case 'information': return 'intel'
    default: return 'neutral'
  }
}

/** Trend direction → severity colour (falling lab usually = improving = safe). */
export function trendSeverity(direction: string, goodWhenFalling = true): Severity {
  const d = (direction || '').toLowerCase()
  if (d === 'falling') return goodWhenFalling ? 'safe' : 'blocker'
  if (d === 'rising') return goodWhenFalling ? 'blocker' : 'safe'
  return 'neutral'
}
