/**
 * IntelligenceWorkflowBits — compact surfaces for the final-wave services
 * ========================================================================
 *   • CounselingScorecard      — #11, post-consult rubric/sentiment scorecard
 *   • IntegrityBanner          — #6, controlled-substance forgery banner
 *   • QueuePriorityBadge       — #2, inline priority pill for the queue
 *   • CompoundingFlags         — #10, inline incompatibility flags
 *
 * All consume the §1.2 envelope and show a TierBadge where appropriate.
 */
import { useQuery } from '@tanstack/react-query'
import { apiClient } from '../lib/api'
import TierBadge from './TierBadge'

interface Env<T> {
  result: T; tier_used: 'local' | 'cloud' | 'hybrid'; degraded: boolean
  confidence: number; options_offline: string[]
}

// ─── #11 Counseling Scorecard ─────────────────────────────────────────────────

interface RubricEl { key: string; label: string; covered: boolean; evidence: string }
interface CounselScore {
  rubric: RubricEl[]; coverage_pct: number; sentiment: string; sentiment_score: number
  comprehension: string; patient_questions: number; duration_seconds: number; flags: string[]
}

export function CounselingScorecard({ transcriptId, transcriptText }: {
  transcriptId?: string; transcriptText?: string
}) {
  const { data: raw } = useQuery({
    queryKey: ['counseling-assess', transcriptId, transcriptText?.slice(0, 32)],
    queryFn:  () => apiClient.post('/intelligence/clinical/counseling/assess', {
      transcript_id: transcriptId, transcript_text: transcriptText,
    }).then(r => r.data as Env<{ score: CounselScore | null; coaching: string }>),
    enabled: !!(transcriptId || transcriptText),
  })
  if (!raw?.result.score) return null
  const s = raw.result.score
  const sentColor = s.sentiment === 'positive' ? 'text-emerald-600'
    : s.sentiment === 'neutral' ? 'text-gray-500' : 'text-red-600'

  return (
    <div className="bg-white border border-gray-200 rounded-xl p-4">
      <div className="flex items-center justify-between mb-3">
        <div className="flex items-center gap-2">
          <span className="text-lg">🗣</span>
          <h3 className="text-sm font-semibold text-gray-800">Counseling Quality</h3>
          <TierBadge tier={raw.tier_used} degraded={raw.degraded} optionsOffline={raw.options_offline} compact />
        </div>
        <div className="text-right">
          <div className={`text-xl font-bold ${s.coverage_pct >= 70 ? 'text-emerald-600' : s.coverage_pct >= 50 ? 'text-amber-600' : 'text-red-600'}`}>
            {s.coverage_pct}%
          </div>
          <div className="text-[10px] text-gray-400">rubric coverage</div>
        </div>
      </div>

      {/* Rubric grid */}
      <div className="grid grid-cols-2 gap-1.5 mb-3">
        {s.rubric.map(r => (
          <div key={r.key} className="flex items-center gap-1.5 text-xs">
            <span className={r.covered ? 'text-emerald-600' : 'text-gray-300'}>{r.covered ? '✓' : '○'}</span>
            <span className={r.covered ? 'text-gray-700' : 'text-gray-400'}>{r.label}</span>
          </div>
        ))}
      </div>

      <div className="flex items-center gap-4 text-xs border-t border-gray-100 pt-2">
        <span>Patient mood: <span className={`font-medium ${sentColor}`}>{s.sentiment}</span></span>
        <span>Understanding: <span className="font-medium text-gray-700">{s.comprehension}</span></span>
        {s.duration_seconds > 0 && <span className="text-gray-400">{Math.round(s.duration_seconds / 60)}m</span>}
      </div>

      {s.flags.length > 0 && (
        <div className="flex flex-wrap gap-1 mt-2">
          {s.flags.map(f => (
            <span key={f} className="text-[10px] bg-amber-50 text-amber-700 border border-amber-200 px-1.5 py-0.5 rounded">
              {f.replace(/_/g, ' ')}
            </span>
          ))}
        </div>
      )}
      {raw.result.coaching && (
        <p className="text-[11px] text-indigo-600 mt-2 italic">💡 {raw.result.coaching}</p>
      )}
    </div>
  )
}

// ─── #6 Integrity Banner (controlled substances only) ─────────────────────────

interface IntegrityResult {
  skipped: boolean; skip_reason?: string; forgery_score: number; risk_band: string
  visual_risk: number; behavioral_risk: number; signals: string[]
  rationale: string; recommended_action: string
}

export function IntegrityBanner({
  deaSchedule, isControlled, docId, prescriberId, ndc, drugName, quantity,
}: {
  deaSchedule?: string; isControlled?: boolean; docId?: string
  prescriberId?: string; ndc?: string; drugName?: string; quantity?: number
}) {
  const { data: raw } = useQuery({
    queryKey: ['integrity', docId, prescriberId, ndc, quantity],
    queryFn:  () => apiClient.post('/intelligence/clinical/integrity/score', {
      dea_schedule: deaSchedule, is_controlled: isControlled, doc_id: docId,
      prescriber_id: prescriberId, ndc, drug_name: drugName, quantity,
    }).then(r => r.data as Env<IntegrityResult>),
    enabled: !!(isControlled || (deaSchedule && deaSchedule.toUpperCase().startsWith('C'))),
  })
  // Only render for controlled substances that were actually scored.
  if (!raw || raw.result.skipped) return null
  const d = raw.result
  const style = d.risk_band === 'critical' ? 'bg-red-50 border-red-300 text-red-800'
    : d.risk_band === 'warning' ? 'bg-amber-50 border-amber-300 text-amber-800'
    : 'bg-emerald-50 border-emerald-200 text-emerald-800'
  const icon = d.risk_band === 'critical' ? '🚨' : d.risk_band === 'warning' ? '⚠' : '🛡'

  return (
    <div className={`rounded-lg border px-3 py-2 ${style}`}>
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <span className="text-lg">{icon}</span>
          <span className="text-sm font-semibold">
            Controlled-Substance Integrity: {Math.round(d.forgery_score * 100)}% risk ({d.risk_band})
          </span>
        </div>
        <TierBadge tier={raw.tier_used} degraded={raw.degraded} optionsOffline={raw.options_offline} compact />
      </div>
      <p className="text-xs mt-1">{d.rationale}</p>
      <div className="flex items-center gap-3 mt-1 text-[10px] opacity-80">
        <span>visual {Math.round(d.visual_risk * 100)}%</span>
        <span>behavioral {Math.round(d.behavioral_risk * 100)}%</span>
        {d.signals.slice(0, 3).map(s => (
          <span key={s} className="bg-white/50 px-1.5 py-0.5 rounded">{s.replace(/_/g, ' ')}</span>
        ))}
      </div>
      {d.risk_band !== 'ok' && (
        <p className="text-[11px] font-medium mt-1">→ {d.recommended_action.replace(/_/g, ' ')}</p>
      )}
    </div>
  )
}

// ─── #2 Queue Priority Badge (inline) ─────────────────────────────────────────

const BAND_PILL: Record<string, string> = {
  urgent: 'bg-red-600 text-white',
  high:   'bg-orange-500 text-white',
  normal: 'bg-blue-100 text-blue-700',
  low:    'bg-gray-100 text-gray-500',
}

export function QueuePriorityBadge({ band, score }: { band: string; score: number }) {
  return (
    <span className={`text-[9px] px-1.5 py-0.5 rounded-full font-semibold uppercase ${BAND_PILL[band] || BAND_PILL.normal}`}
      title={`Priority score ${Math.round(score * 100)}%`}>
      {band}
    </span>
  )
}

// ─── #10 Compounding Flags (inline list) ──────────────────────────────────────

interface Finding { severity: string; kind: string; ingredients: string[]; message: string; source: string }

export function CompoundingFlags({ ingredients, formulaBudDays, patientWeightKg }: {
  ingredients: { name: string; quantity?: number; unit?: string; bud_days?: number }[]
  formulaBudDays?: number; patientWeightKg?: number
}) {
  const { data: raw } = useQuery({
    queryKey: ['compound-compat', JSON.stringify(ingredients), formulaBudDays],
    queryFn:  () => apiClient.post('/intelligence/clinical/compound/compatibility', {
      ingredients, formula_bud_days: formulaBudDays, patient_weight_kg: patientWeightKg,
    }).then(r => r.data as Env<{ findings: Finding[]; summary: Record<string, number> }>),
    enabled: ingredients.length >= 1,
  })
  if (!raw) return null
  const { findings, summary } = raw.result

  const sevStyle: Record<string, string> = {
    critical: 'bg-red-50 border-red-200 text-red-700',
    warning:  'bg-amber-50 border-amber-200 text-amber-700',
    info:     'bg-blue-50 border-blue-200 text-blue-700',
  }

  return (
    <div className="bg-white border border-gray-200 rounded-xl p-3">
      <div className="flex items-center justify-between mb-2">
        <div className="flex items-center gap-2">
          <span className="text-base">🧪</span>
          <h3 className="text-sm font-semibold text-gray-800">Compatibility Check</h3>
          <TierBadge tier={raw.tier_used} degraded={raw.degraded} optionsOffline={raw.options_offline} compact />
        </div>
        <div className="flex gap-1.5 text-[10px]">
          {summary.critical > 0 && <span className="text-red-600 font-bold">{summary.critical} critical</span>}
          {summary.warning > 0 && <span className="text-amber-600">{summary.warning} warning</span>}
        </div>
      </div>
      {findings.length === 0 ? (
        <p className="text-xs text-emerald-600">✓ No incompatibilities detected.</p>
      ) : (
        <div className="space-y-1.5">
          {findings.map((f, i) => (
            <div key={i} className={`rounded-lg border px-2.5 py-1.5 ${sevStyle[f.severity] || sevStyle.info}`}>
              <div className="flex items-center gap-2">
                <span className="text-[10px] font-bold uppercase">{f.kind}</span>
                <span className="text-[9px] opacity-60">via {f.source}</span>
              </div>
              <p className="text-xs mt-0.5">{f.message}</p>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}
