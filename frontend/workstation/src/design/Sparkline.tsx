/**
 * Sparkline — pure-SVG inline trend, sized for metric chips (default 36px tall).
 * Line draws on mount via stroke-dashoffset (CSS), honouring reduced-motion.
 * No recharts/ResponsiveContainer overhead at this scale.
 */
import { useEffect, useId, useRef, useState } from 'react'

interface Props {
  values: number[]
  width?: number
  height?: number
  color?: string
  /** show the last point as a glowing dot */
  showHead?: boolean
  /** subtle area fill under the line */
  fill?: boolean
  className?: string
}

export default function Sparkline({
  values, width = 88, height = 36, color = '#60a5fa', showHead = true, fill = true, className,
}: Props) {
  const gid = useId().replace(/:/g, '')
  const pathRef = useRef<SVGPathElement>(null)
  const [len, setLen] = useState(0)

  useEffect(() => {
    if (pathRef.current) setLen(pathRef.current.getTotalLength())
  }, [values])

  if (!values || values.length < 2) {
    return <svg width={width} height={height} className={className} aria-hidden="true" />
  }

  const min = Math.min(...values)
  const max = Math.max(...values)
  const span = max - min || 1
  const pad = 3
  const stepX = (width - pad * 2) / (values.length - 1)
  const pts = values.map((v, i) => {
    const x = pad + i * stepX
    const y = pad + (height - pad * 2) * (1 - (v - min) / span)
    return [x, y] as const
  })
  const line = pts.map(([x, y], i) => `${i ? 'L' : 'M'}${x.toFixed(1)},${y.toFixed(1)}`).join(' ')
  const area = `${line} L${pts[pts.length - 1][0].toFixed(1)},${height - pad} L${pts[0][0].toFixed(1)},${height - pad} Z`
  const [hx, hy] = pts[pts.length - 1]

  return (
    <svg width={width} height={height} className={className} aria-hidden="true">
      <defs>
        <linearGradient id={`sl-${gid}`} x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stopColor={color} stopOpacity={0.28} />
          <stop offset="100%" stopColor={color} stopOpacity={0} />
        </linearGradient>
      </defs>
      {fill && <path d={area} fill={`url(#sl-${gid})`} />}
      <path
        ref={pathRef}
        d={line}
        fill="none"
        stroke={color}
        strokeWidth={1.75}
        strokeLinecap="round"
        strokeLinejoin="round"
        style={len ? {
          strokeDasharray: len,
          strokeDashoffset: len,
          animation: 'mc-sl-draw 600ms cubic-bezier(0.22,1,0.36,1) forwards',
        } : undefined}
      />
      {showHead && (
        <circle cx={hx} cy={hy} r={2.2} fill={color}>
          <animate attributeName="opacity" values="0;1" dur="200ms" begin="450ms" fill="freeze" />
        </circle>
      )}
      <style>{`@keyframes mc-sl-draw { to { stroke-dashoffset: 0; } }
        @media (prefers-reduced-motion: reduce){ path[style]{ animation:none!important; stroke-dashoffset:0!important; } }`}</style>
    </svg>
  )
}
