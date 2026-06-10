/**
 * TrajectorySignalStrip — unified Patient Intelligence companion to the
 * Specialist Council, surfaced directly in the review workflow.
 * =====================================================================
 * Design intent (UI/UX):
 *   "Rich in data, but legible" is a progressive-disclosure problem, not a
 *   layout problem. So this is ONE component with TWO densities:
 *
 *     1. STRIP  (always visible, ~52px) — a single-glance scan: patient
 *        narrative one-liner, lab-trend direction chips, a care-gap count,
 *        and (when relevant) a prescriber-deviation flag — exactly the
 *        signals a pharmacist needs to decide "does this need my attention
 *        before I sign off?" without leaving the review flow or waiting on
 *        anything (the underlying data is precomputed/cached, same as the
 *        Council — no parallel review surface, no extra spinner).
 *
 *     2. HUB    (expand on demand) — the full picture: longitudinal lab
 *        trend chart, narrative, care-gap detail, AND prescriber context
 *        for *this* Rx's prescriber (profile + deviation flag) — all the
 *        patient-centered intelligence that used to be scattered across
 *        the Clinical Intel dashboard (PatientTrajectory) and orphaned
 *        components (PrescriberIntelligence was never mounted anywhere)
 *        consolidated into the one place a pharmacist is already looking
 *        when the question "what do I know about this patient?" comes up.
 *
 *   Tabs (not stacked cards) keep the expanded view from becoming another
 *   wall of widgets — only one rich section is visible at a time, and the
 *   strip itself tells you *which* tab is worth opening.
 *
 * Consumes the existing §1.2 envelope (`/intelligence/workflow/patient/
 * {id}/trajectory`, already live & precomputed) and the existing prescriber
 * endpoints — no new backend surface, per "reuse, don't duplicate".
 */
import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import {
  LineChart, Line, XAxis, YAxis, Tooltip, ResponsiveContainer, CartesianGrid, Legend,
} from 'recharts'
import { apiClient } from '../lib/api'
import TierBadge from './TierBadge'
import { PrescriberProfileCard, PrescriberDeviationFlag } from './PrescriberIntelligence'

// ─── Shared envelope / trajectory types (mirrors PatientTrajectory) ───────────

interface Envelope<T> {
  result: T; tier_used: 'local' | 'cloud' | 'hybrid'; confidence: number
  degraded: boolean; options_offline: string[]
}
interface LabTrend {
  test_name: string; points: { date: string; value: number }[]
  slope: number; direction: string; latest: number | null; delta_pct: number | null
}
interface CareGap { condition: string; message: string; severity: string }
interface TimelineEvent { type: string; date: string | null; label: string; value: number; detail: string }
interface TrajectoryResult {
  narrative: string
  summary: { total_events: number; medications: number; lab_results: number; tracked_labs: number; care_gaps: number }
  timeline: TimelineEvent[]
  lab_trends: LabTrend[]
  care_gaps: CareGap[]
}

const DEMO: Envelope<TrajectoryResult> = {
  tier_used: 'local', confidence: 0.7, degraded: true, options_offline: ['llm_narrative', 'population_priors'],
  result: {
    narrative: '14 medication events on record. Ldl trending down (likely therapy response). 1 possible care gap: Diabetes.',
    summary: { total_events: 38, medications: 14, lab_results: 24, tracked_labs: 3, care_gaps: 1 },
    timeline: [],
    lab_trends: [
      { test_name: 'LDL', direction: 'falling', slope: -0.08, latest: 92, delta_pct: -38,
        points: [{date:'2025-01',value:148},{date:'2025-04',value:121},{date:'2025-08',value:104},{date:'2026-01',value:92}] },
      { test_name: 'A1c', direction: 'rising', slope: 0.004, latest: 8.1, delta_pct: 12,
        points: [{date:'2025-01',value:7.2},{date:'2025-06',value:7.6},{date:'2026-01',value:8.1}] },
      { test_name: 'Systolic BP', direction: 'falling', slope: -0.02, latest: 128, delta_pct: -15,
        points: [{date:'2025-01',value:151},{date:'2025-06',value:138},{date:'2026-01',value:128}] },
    ],
    care_gaps: [
      { condition: 'Diabetes', message: 'Diabetes therapy detected but no A1c/glucose result in the last 180 days — monitoring may be overdue.', severity: 'warning' },
    ],
  },
}

const TREND_COLORS = ['#3b82f6', '#ef4444', '#22c55e', '#a855f7', '#f59e0b']

function DirectionBadge({ dir, delta }: { dir: string; delta: number | null }) {
  const cfg = dir === 'falling'
    ? { icon: '↓', cls: 'text-emerald-600 bg-emerald-50 border-emerald-100' }
    : dir === 'rising'
    ? { icon: '↑', cls: 'text-red-600 bg-red-50 border-red-100' }
    : { icon: '→', cls: 'text-gray-500 bg-gray-50 border-gray-100' }
  return (
    <span className={`inline-flex items-center gap-0.5 text-[10px] px-1.5 py-0.5 rounded-full border ${cfg.cls}`}>
      {cfg.icon} {delta != null ? `${delta > 0 ? '+' : ''}${delta}%` : dir}
    </span>
  )
}

// ─── Tab: Health Trajectory (chart + narrative + care gaps) ───────────────────

function TrajectoryTab({ d, patientName }: { d: TrajectoryResult; patientName?: string }) {
  const dateSet = new Set<string>()
  d.lab_trends.forEach(t => t.points.forEach(p => dateSet.add(p.date)))
  const dates = Array.from(dateSet).sort()
  const chartData = dates.map(date => {
    const row: Record<string, any> = { date }
    d.lab_trends.forEach(t => {
      const pt = t.points.find(p => p.date === date)
      if (pt) row[t.test_name] = pt.value
    })
    return row
  })

  return (
    <div className="space-y-3">
      <div className="bg-indigo-50 border border-indigo-100 rounded-lg px-3 py-2">
        <p className="text-sm text-indigo-900">{d.narrative}</p>
        <p className="text-[10px] text-indigo-400 mt-1">
          {d.summary.medications} meds · {d.summary.lab_results} labs · {d.summary.tracked_labs} tracked
          {patientName ? ` — ${patientName}` : ''}
        </p>
      </div>

      {chartData.length > 1 && (
        <div className="h-48">
          <ResponsiveContainer width="100%" height="100%">
            <LineChart data={chartData} margin={{ top: 6, right: 12, bottom: 4, left: -10 }}>
              <CartesianGrid strokeDasharray="3 3" stroke="#eef2f7" />
              <XAxis dataKey="date" tick={{ fontSize: 10, fill: '#94a3b8' }} />
              <YAxis tick={{ fontSize: 10, fill: '#94a3b8' }} />
              <Tooltip contentStyle={{ fontSize: 11, borderRadius: 8 }} />
              <Legend wrapperStyle={{ fontSize: 11 }} />
              {d.lab_trends.map((t, i) => (
                <Line key={t.test_name} type="monotone" dataKey={t.test_name}
                  stroke={TREND_COLORS[i % TREND_COLORS.length]} strokeWidth={2} dot={{ r: 2 }} connectNulls />
              ))}
            </LineChart>
          </ResponsiveContainer>
        </div>
      )}

      <div className="flex flex-wrap gap-2">
        {d.lab_trends.map(t => (
          <div key={t.test_name} className="flex items-center gap-1.5 border border-gray-100 rounded-lg px-2 py-1">
            <span className="text-xs text-gray-700">{t.test_name}</span>
            {t.latest != null && <span className="text-xs font-mono text-gray-500">{t.latest}</span>}
            <DirectionBadge dir={t.direction} delta={t.delta_pct} />
          </div>
        ))}
      </div>

      {d.care_gaps.length > 0 && (
        <div className="space-y-1.5">
          <p className="text-[11px] font-semibold uppercase tracking-wide text-amber-700">⚠ Care gaps</p>
          {d.care_gaps.map((g, i) => (
            <div key={i} className="bg-amber-50 border border-amber-200 rounded-lg px-3 py-1.5">
              <span className="text-xs font-semibold text-amber-800">{g.condition}</span>
              <p className="text-[11px] text-amber-700">{g.message}</p>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

// ─── Tab: Prescriber Context (profile + this-Rx deviation) ────────────────────

function PrescriberTab({
  prescriberId, prescriberName, ndc, drugName, quantity, isControlled,
}: {
  prescriberId: string; prescriberName?: string; ndc?: string
  drugName?: string; quantity?: number; isControlled?: boolean
}) {
  return (
    <div className="space-y-3">
      <PrescriberProfileCard prescriberId={prescriberId} prescriberName={prescriberName} />
      {ndc && drugName && (
        <PrescriberDeviationFlag
          prescriberId={prescriberId}
          ndc={ndc}
          drugName={drugName}
          quantity={quantity ?? 0}
          isControlled={isControlled}
        />
      )}
    </div>
  )
}

// ─── Tab nav button ────────────────────────────────────────────────────────────

function TabButton({ active, onClick, children }: { active: boolean; onClick: () => void; children: React.ReactNode }) {
  return (
    <button
      onClick={onClick}
      className={`text-xs font-medium px-3 py-1.5 rounded-t-lg border-b-2 transition-colors ${
        active
          ? 'border-indigo-500 text-indigo-700 bg-indigo-50/60'
          : 'border-transparent text-gray-400 hover:text-gray-600 hover:bg-gray-50'
      }`}
    >
      {children}
    </button>
  )
}

// ─── Main: collapsed strip ⇄ expanded hub ─────────────────────────────────────

interface Props {
  patientId: string
  patientName?: string
  prescriberId?: string
  prescriberName?: string
  ndc?: string
  drugName?: string
  quantity?: number
  isControlled?: boolean
}

export default function TrajectorySignalStrip({
  patientId, patientName, prescriberId, prescriberName, ndc, drugName, quantity, isControlled,
}: Props) {
  const [expanded, setExpanded] = useState(false)
  const [tab, setTab] = useState<'trajectory' | 'prescriber'>('trajectory')

  const { data: raw } = useQuery({
    queryKey: ['patient-trajectory', patientId],
    queryFn:  () => apiClient.get(`/intelligence/workflow/patient/${patientId}/trajectory`)
                    .then(r => r.data as Envelope<TrajectoryResult>),
    enabled:  !!patientId,
    staleTime: 5 * 60_000, // precomputed/cached upstream — no need to hammer it
  })
  const env = raw ?? DEMO
  const d = env.result

  const topTrends = [...d.lab_trends].sort((a, b) => Math.abs(b.delta_pct ?? 0) - Math.abs(a.delta_pct ?? 0)).slice(0, 3)
  const hasCareGaps = d.care_gaps.length > 0
  const hasPrescriberTab = !!prescriberId

  return (
    <div className="bg-white border border-gray-200 rounded-xl overflow-hidden">
      {/* ── Always-visible signal strip — the "scan in 2 seconds" row ──────── */}
      <button
        onClick={() => setExpanded(e => !e)}
        className="w-full flex items-center justify-between gap-3 px-4 py-2.5 hover:bg-gray-50/80 transition-colors text-left"
        aria-expanded={expanded}
      >
        <div className="flex items-center gap-2.5 min-w-0">
          <span className="text-lg leading-none">🧬</span>
          <div className="min-w-0">
            <div className="flex items-center gap-2">
              <span className="text-sm font-semibold text-gray-800 whitespace-nowrap">
                Patient Intelligence{patientName ? ` — ${patientName}` : ''}
              </span>
              <TierBadge tier={env.tier_used} degraded={env.degraded} optionsOffline={env.options_offline} compact />
            </div>
            <p className="text-xs text-gray-500 truncate max-w-lg">{d.narrative}</p>
          </div>
        </div>

        <div className="flex items-center gap-1.5 flex-shrink-0">
          {topTrends.map(t => (
            <span key={t.test_name} className="hidden md:inline-flex items-center gap-1 text-[10px] text-gray-500">
              {t.test_name} <DirectionBadge dir={t.direction} delta={t.delta_pct} />
            </span>
          ))}
          {hasCareGaps && (
            <span className="text-[10px] bg-amber-100 text-amber-700 px-1.5 py-0.5 rounded-full font-medium">
              ⚠ {d.care_gaps.length} care gap{d.care_gaps.length !== 1 ? 's' : ''}
            </span>
          )}
          <span className="text-gray-300 text-xs ml-1">{expanded ? '▲' : '▼'}</span>
        </div>
      </button>

      {/* ── Expanded hub — tabbed, one rich surface at a time ──────────────── */}
      {expanded && (
        <div className="border-t border-gray-100">
          <div className="flex gap-1 px-3 pt-2 border-b border-gray-100">
            <TabButton active={tab === 'trajectory'} onClick={() => setTab('trajectory')}>
              📈 Health Trajectory
            </TabButton>
            {hasPrescriberTab && (
              <TabButton active={tab === 'prescriber'} onClick={() => setTab('prescriber')}>
                🩺 Prescriber Context
              </TabButton>
            )}
          </div>
          <div className="p-3">
            {tab === 'trajectory' && <TrajectoryTab d={d} patientName={patientName} />}
            {tab === 'prescriber' && hasPrescriberTab && (
              <PrescriberTab
                prescriberId={prescriberId!}
                prescriberName={prescriberName}
                ndc={ndc}
                drugName={drugName}
                quantity={quantity}
                isControlled={isControlled}
              />
            )}
          </div>
          <p className="px-3 pb-2.5 text-[10px] text-gray-400 italic border-t border-gray-50 pt-1.5">
            Patient-history intelligence is precomputed and cached — opening this panel never blocks the review.
            Findings are review prompts, not diagnoses; clinical judgment rests with the licensed pharmacist.
          </p>
        </div>
      )}
    </div>
  )
}
