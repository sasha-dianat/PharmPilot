/**
 * CountUp — animates a number from its previous value to the next on change
 * (400ms, ease-out) via requestAnimationFrame. Tabular figures so width never
 * jitters. Honours prefers-reduced-motion (snaps instantly).
 */
import { useEffect, useRef, useState } from 'react'

interface Props {
  value: number
  decimals?: number
  duration?: number
  prefix?: string
  suffix?: string
  className?: string
}

const prefersReduced = () =>
  typeof window !== 'undefined' &&
  window.matchMedia?.('(prefers-reduced-motion: reduce)').matches

export default function CountUp({ value, decimals = 0, duration = 400, prefix = '', suffix = '', className }: Props) {
  const [display, setDisplay] = useState(value)
  const fromRef = useRef(value)
  const rafRef = useRef<number>(0)

  useEffect(() => {
    const from = fromRef.current
    const to = value
    if (from === to) return
    if (prefersReduced()) { setDisplay(to); fromRef.current = to; return }

    const start = performance.now()
    const tick = (now: number) => {
      const t = Math.min(1, (now - start) / duration)
      const eased = 1 - Math.pow(1 - t, 3)
      setDisplay(from + (to - from) * eased)
      if (t < 1) rafRef.current = requestAnimationFrame(tick)
      else fromRef.current = to
    }
    rafRef.current = requestAnimationFrame(tick)
    return () => cancelAnimationFrame(rafRef.current)
  }, [value, duration])

  return (
    <span className={className} style={{ fontVariantNumeric: 'tabular-nums' }}>
      {prefix}{display.toFixed(decimals)}{suffix}
    </span>
  )
}
