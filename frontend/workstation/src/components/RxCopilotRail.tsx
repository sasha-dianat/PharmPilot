/**
 * RxCopilotRail — #15 End-to-End Rx Workflow Intelligent Automation
 * ==================================================================
 * A compact rail showing each workflow step's automation-confidence, what the AI
 * recommends, and a one-click auto-advance (audited + reversible). AI proposes;
 * the pharmacist is always the accountable signer — nothing auto-dispenses.
 *
 * Consumes the §1.2 envelope; shows a TierBadge.
 */
import { useEffect, useState } from 'react'
import { useQuery, useMutation } from '@tanstack/react-query'
import { apiClient } from '../lib/api'
import TierBadge from './TierBadge'

interface Step {
  step: string; confidence: number; can_auto: boolean
  recommendation: string; rationale: string; signals: string[]
}
interface CopilotResult {
  rx_id: string; rx_number: string; drug_name: string; is_controlled: boolean
  steps: Step[]; thresholds: Record<string, number>
  auto_clearable_steps: number; overall: string; auto_executable_steps: string[]
  controlled_substance_alert: {
    level: 'warning'
    code: 'controlled_substance'
    dea_schedule: string | null
    message: string
  } | null
}
interface Envelope {
  result: CopilotResult; tier_used: 'local' | 'cloud' | 'hybrid'
  degraded: boolean; confidence: number; options_offline: string[]
}
interface AutoAdvanceResponse {
  ok: boolean
  step: string
  confidence?: number
  action_id?: string
  audited?: boolean
  reversible?: boolean
  reason?: string
}
interface UndoResponse {
  ok: boolean
  action_id: string
  step?: string
  reason?: string
}

const DEMO: Envelope = {
  tier_used: 'local', degraded: true, confidence: 0.83, options_offline: ['cloud_second_opinion', 'live_adjudication'],
  result: {
    rx_id: 'demo', rx_number: 'RX-00012345', drug_name: 'Lisinopril 10mg', is_controlled: false,
    auto_clearable_steps: 2, overall: 'mostly_automatable',
    auto_executable_steps: ['adjudication'], controlled_substance_alert: null,
    thresholds: { dur: 0.95, adjudication: 0.95, verification: 0.90 },
    steps: [
      { step: 'dur', confidence: 0.97, can_auto: true, recommendation: 'auto_clear',
        rationale: 'Confidence 97% ≥ threshold 95% — safe to auto-advance (pharmacist may undo).',
        signals: ['stable_history_6_fills'] },
      { step: 'adjudication', confidence: 0.96, can_auto: true, recommendation: 'auto_clear',
        rationale: 'Confidence 96% ≥ threshold 95% — safe to auto-advance.',
        signals: ['history_42_claims_1_rejected'] },
      { step: 'verification', confidence: 0.74, can_auto: false, recommendation: 'review',
        rationale: 'Confidence 74% below auto threshold — quick review advised.',
        signals: ['package_match_0.74'] },
    ],
  },
}

const STEP_LABEL: Record<string, string> = {
  dur: 'DUR Safety', adjudication: 'Adjudication', verification: 'Verification',
}
const REC_STYLE: Record<string, { cls: string; icon: string; text: string }> = {
  auto_clear: { cls: 'bg-emerald-50 border-emerald-200 text-emerald-700', icon: '✓', text: 'Auto-clear' },
  review:     { cls: 'bg-amber-50 border-amber-200 text-amber-700',       icon: '👁', text: 'Review' },
  hold:       { cls: 'bg-red-50 border-red-200 text-red-700',             icon: '✋', text: 'Hold' },
}
const ADVANCE_REASON: Record<string, string> = {
  manual_step_decision_aid_only: 'Decision aid only in pilot phase. Advance manually.',
  below_threshold: 'Confidence is below the automation threshold.',
  rx_not_in_expected_state: 'Rx is no longer in the expected workflow state.',
  transition_refused: 'Workflow transition was refused.',
}
const UNDO_REASON: Record<string, string> = {
  already_reverted: 'This auto-action was already undone.',
  undo_partially_failed: 'Undo partially failed. Review the Rx state before continuing.',
  rx_not_in_expected_state: 'Rx is no longer in the expected workflow state.',
}

const reasonText = (reason: string | undefined, map: Record<string, string>) =>
  reason ? (map[reason] ?? reason) : 'Action failed.'
const errorText = (err: unknown) => {
  const detail = (err as { response?: { data?: { detail?: unknown } } })?.response?.data?.detail
  return typeof detail === 'string' ? detail : 'Action failed. Check permissions and Rx state.'
}

function ConfRing({ value, can }: { value: number; can: boolean }) {
  const pct = Math.round(value * 100)
  const col = can ? '#10b981' : pct >= 70 ? '#f59e0b' : '#ef4444'
  return (
    <div className="relative w-10 h-10 flex items-center justify-center">
      <svg className="w-10 h-10 -rotate-90" viewBox="0 0 36 36">
        <circle cx="18" cy="18" r="15" fill="none" stroke="#f1f5f9" strokeWidth="3" />
        <circle cx="18" cy="18" r="15" fill="none" stroke={col} strokeWidth="3"
          strokeDasharray={`${pct * 0.94} 100`} strokeLinecap="round" />
      </svg>
      <span className="absolute text-[10px] font-bold" style={{ color: col }}>{pct}</span>
    </div>
  )
}

export default function RxCopilotRail({ rxId }: { rxId: string }) {
  const [actionByStep, setActionByStep] = useState<Record<string, string>>({})
  const [stepErrors, setStepErrors] = useState<Record<string, string>>({})
  useEffect(() => {
    setActionByStep({})
    setStepErrors({})
  }, [rxId])
  const { data: raw, refetch } = useQuery({
    queryKey: ['rx-copilot', rxId],
    queryFn:  () => apiClient.get(`/intelligence/workflow/rx/${rxId}/copilot`)
                    .then(r => r.data as Envelope),
    enabled:  !!rxId,
  })
  const advanceMutation = useMutation({
    mutationFn: (step: string) => apiClient.post(`/intelligence/workflow/rx/${rxId}/auto-advance`, { step })
                  .then(r => r.data as AutoAdvanceResponse),
    onSuccess: (result, step) => {
      if (!result.ok) {
        setStepErrors(prev => ({ ...prev, [step]: reasonText(result.reason, ADVANCE_REASON) }))
        return
      }
      setStepErrors(prev => ({ ...prev, [step]: '' }))
      if (result.action_id) {
        setActionByStep(prev => ({ ...prev, [step]: result.action_id! }))
      }
      refetch()
    },
    onError: (err, step) => {
      setStepErrors(prev => ({ ...prev, [step]: errorText(err) }))
    },
  })
  const undoMutation = useMutation({
    mutationFn: ({ actionId }: { step: string; actionId: string }) =>
      apiClient.post(`/intelligence/workflow/rx/${rxId}/auto-advance/undo`, { action_id: actionId })
        .then(r => r.data as UndoResponse),
    onSuccess: (result, vars) => {
      if (!result.ok) {
        setStepErrors(prev => ({ ...prev, [vars.step]: reasonText(result.reason, UNDO_REASON) }))
        return
      }
      setStepErrors(prev => ({ ...prev, [vars.step]: '' }))
      setActionByStep(prev => {
        const next = { ...prev }
        delete next[vars.step]
        return next
      })
      refetch()
    },
    onError: (err, vars) => {
      setStepErrors(prev => ({ ...prev, [vars.step]: errorText(err) }))
    },
  })

  const env = raw ?? DEMO
  const d = env.result
  const alert = d.controlled_substance_alert

  return (
    <div className="bg-white border border-gray-200 rounded-xl p-3">
      <div className="flex items-center justify-between mb-2">
        <div className="flex items-center gap-2">
          <span className="text-base">🤖</span>
          <h3 className="text-sm font-semibold text-gray-800">Rx Copilot</h3>
          <TierBadge tier={env.tier_used} degraded={env.degraded} optionsOffline={env.options_offline} compact />
        </div>
        <span className={`text-[10px] px-2 py-0.5 rounded-full ${
          d.overall === 'mostly_automatable' ? 'bg-emerald-100 text-emerald-700'
          : d.overall === 'partial' ? 'bg-amber-100 text-amber-700' : 'bg-gray-100 text-gray-600'}`}>
          {d.auto_clearable_steps}/3 auto
        </span>
      </div>

      {alert && (
        <div className="mb-2 rounded-lg border-2 border-red-300 bg-red-50 px-3 py-2 shadow-sm">
          <div className="flex items-start justify-between gap-3">
            <div className="min-w-0">
              <div className="text-[11px] font-bold uppercase tracking-wide text-red-700">
                Controlled substance alert
              </div>
              <p className="mt-0.5 text-xs font-semibold leading-snug text-red-900">{alert.message}</p>
            </div>
            <span className="flex-shrink-0 rounded-full border border-red-300 bg-white px-2 py-0.5 text-[10px] font-bold text-red-700">
              DEA {alert.dea_schedule ?? 'N/A'}
            </span>
          </div>
        </div>
      )}

      <div className="space-y-2">
        {d.steps.map(s => {
          const rec = REC_STYLE[s.recommendation] ?? REC_STYLE.review
          const canExecute = s.can_auto && d.auto_executable_steps.includes(s.step)
          const actionId = actionByStep[s.step]
          const stepError = stepErrors[s.step]
          return (
            <div key={s.step} className={`rounded-lg border px-2.5 py-2 ${rec.cls}`}>
              <div className="flex items-center gap-2.5">
                <ConfRing value={s.confidence} can={s.can_auto} />
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-2">
                    <span className="text-xs font-semibold text-gray-800">{STEP_LABEL[s.step] || s.step}</span>
                    <span className="text-[10px]">{rec.icon} {rec.text}</span>
                  </div>
                  <p className="text-[10px] text-gray-500 leading-tight mt-0.5">{s.rationale}</p>
                </div>
                {canExecute && (
                  <button
                    onClick={() => advanceMutation.mutate(s.step)}
                    disabled={advanceMutation.isPending}
                    className="text-[10px] px-2 py-1 bg-emerald-600 text-white rounded hover:bg-emerald-700 whitespace-nowrap disabled:opacity-50">
                    Auto ▶
                  </button>
                )}
                {s.can_auto && !canExecute && (
                  <span
                    title="Decision aid only in pilot phase — advance manually"
                    className="text-[10px] px-2 py-1 bg-white/70 border border-amber-300 text-amber-700 rounded-full whitespace-nowrap font-medium">
                    Advisory
                  </span>
                )}
              </div>
              {actionId && (
                <div className="mt-2 flex items-center justify-end gap-2">
                  <span className="text-[10px] text-emerald-700 font-medium">Audited &amp; reversible</span>
                  <button
                    onClick={() => undoMutation.mutate({ step: s.step, actionId })}
                    disabled={undoMutation.isPending}
                    className="text-[10px] px-2 py-1 bg-white border border-emerald-300 text-emerald-700 rounded hover:bg-emerald-50 whitespace-nowrap disabled:opacity-50">
                    Undo
                  </button>
                </div>
              )}
              {stepError && (
                <p className="mt-1.5 text-[10px] font-medium text-red-700">{stepError}</p>
              )}
            </div>
          )
        })}
      </div>

      <p className="text-[10px] text-gray-400 mt-2">
        AI proposes — pharmacist confirms. Every auto-action is audited and reversible.
        No auto-dispense.
      </p>
    </div>
  )
}
