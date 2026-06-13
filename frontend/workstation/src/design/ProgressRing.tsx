/**
 * ProgressRing — radial progress (adherence / PDC / token usage).
 * SVG ring that sweeps from 0 on mount via stroke-dashoffset; centre slot for
 * a value label. Reduced-motion snaps to final.
 */
import { useEffect, useState } from 'react'

interface Props {
  /** 0..1 */
  value: number
  size?: number
  stroke?: number
  color?: string
  trackColor?: string
  label?: React.ReactNode
  className?: string
}

export default function ProgressRing({
  value, size = 44, stroke = 4, color = '#34d399', trackColor = 'rgba(255,255,255,0.08)', label, className,
}: Props) {
  const clamped = Math.max(0, Math.min(1, value))
  const r = (size - stroke) / 2
  const c = 2 * Math.PI * r
  const [shown, setShown] = useState(0)

  useEffect(() => {
    const reduced = window.matchMedia?.('(prefers-reduced-motion: reduce)').matches
    if (reduced) { setShown(clamped); return }
    const id = requestAnimationFrame(() => setShown(clamped))
    return () => cancelAnimationFrame(id)
  }, [clamped])

  return (
    <div className={`relative inline-flex items-center justify-center ${className ?? ''}`} style={{ width: size, height: size }}>
      <svg width={size} height={size} className="-rotate-90">
        <circle cx={size / 2} cy={size / 2} r={r} fill="none" stroke={trackColor} strokeWidth={stroke} />
        <circle
          cx={size / 2} cy={size / 2} r={r} fill="none" stroke={color} strokeWidth={stroke} strokeLinecap="round"
          strokeDasharray={c}
          strokeDashoffset={c * (1 - shown)}
          style={{ transition: 'stroke-dashoffset 700ms cubic-bezier(0.22,1,0.36,1)' }}
        />
      </svg>
      {label != null && (
        <span className="absolute inset-0 flex items-center justify-center mc-data text-[10px] font-semibold" style={{ color }}>
          {label}
        </span>
      )}
    </div>
  )
}
