/**
 * DUROverrideModal — Phase 31
 * ============================
 * Shown when a DUR alert fires and the pharmacist chooses to override it
 * rather than cancel the Rx.
 *
 * Captures:
 *   • Alert type + machine-generated alert detail (pre-filled from DUR engine)
 *   • Override reason code (dropdown from API catalogue)
 *   • Clinical notes (free text)
 *   • Prescriber callback flag + notes
 *
 * Behaviour:
 *   • POST /api/v1/dur/overrides → 201
 *   • onOverrideComplete(overrideId) is called; parent proceeds to dispense
 *   • Cannot submit without selecting a reason code
 *   • Prescriber callback section expands when checkbox is ticked
 */
import { useState, useEffect } from 'react'
import { useMutation, useQuery } from '@tanstack/react-query'
import { apiClient } from '../lib/api'
import TierBadge from './TierBadge'

// ─── Types ────────────────────────────────────────────────────────────────────

interface ReasonCode {
  code:  string
  label: string
}

interface OverrideRecord {
  id:                  string
  rx_id:               string
  alert_type:          string
  reason_code:         string
  reason_label:        string
  prescriber_callback: boolean
  created_at:          string
}

export interface DURAlert {
  alert_type:   string   // DDI | ALLERGY | DUPLICATE | DOSE_HIGH | …
  alert_detail: string   // human-readable message from DUR engine
  severity?:    'critical' | 'major' | 'moderate' | 'minor'
}

interface Props {
  rxId:      string
  rxNumber:  string
  alert:     DURAlert
  drugName?: string   // enables a more specific AI reason suggestion
  onOverrideComplete: (overrideId: string) => void
  onCancel:  () => void
}

interface ReasonSuggestion {
  reason_code:  string
  reason_label: string
  probability:  number
  support:      number
  basis:        string
}

// ─── Demo data ────────────────────────────────────────────────────────────────

const DEMO_REASON_CODES: ReasonCode[] = [
  { code: 'PROF_JUDGMENT',   label: 'Professional judgement — risk-benefit acceptable' },
  { code: 'PRESCRIBER_AUTH', label: 'Prescriber authorised override (verbal/written)' },
  { code: 'DUPLICATE_OK',    label: 'Duplicate therapy intentional — different indication' },
  { code: 'ALLERGY_REFUTED', label: 'Documented allergy is refuted / mislabelled' },
  { code: 'DOSE_TITRATION',  label: 'High dose is intentional titration per protocol' },
  { code: 'DOSE_ADJUST',     label: 'Dose adjusted for renal/hepatic impairment' },
  { code: 'AGE_EXCEPTION',   label: 'Age-related alert — paediatric/geriatric dosing confirmed' },
  { code: 'PREGNANCY_OK',    label: 'Pregnancy risk accepted — benefit outweighs risk' },
  { code: 'SHORT_COURSE',    label: 'Short-course therapy — interaction risk negligible' },
  { code: 'PATIENT_CONSENT', label: 'Patient counselled and consented to risk' },
  { code: 'KNOWN_TOLERANCE', label: 'Patient has documented tolerance to combination' },
  { code: 'FORMULARY_REQ',   label: 'Formulary / insurance requirement overrides preferred agent' },
  { code: 'OTHER',           label: 'Other — see clinical notes' },
]

// Alert type → icon + colour palette
const ALERT_STYLE: Record<string, { icon: string; colour: string; bg: string }> = {
  DDI:           { icon: '⚡', colour: 'text-red-700',    bg: 'bg-red-50 border-red-200'    },
  ALLERGY:       { icon: '🚨', colour: 'text-red-700',    bg: 'bg-red-50 border-red-200'    },
  DUPLICATE:     { icon: '📋', colour: 'text-amber-700',  bg: 'bg-amber-50 border-amber-200'},
  DOSE_HIGH:     { icon: '⬆',  colour: 'text-orange-700', bg: 'bg-orange-50 border-orange-200'},
  DOSE_LOW:      { icon: '⬇',  colour: 'text-blue-700',   bg: 'bg-blue-50 border-blue-200'  },
  AGE:           { icon: '👤', colour: 'text-purple-700', bg: 'bg-purple-50 border-purple-200'},
  PREGNANCY:     { icon: '🤰', colour: 'text-pink-700',   bg: 'bg-pink-50 border-pink-200'  },
  RENAL:         { icon: '🔬', colour: 'text-teal-700',   bg: 'bg-teal-50 border-teal-200'  },
  HEPATIC:       { icon: '🔬', colour: 'text-teal-700',   bg: 'bg-teal-50 border-teal-200'  },
  QTPROLONGATION:{ icon: '💓', colour: 'text-red-700',    bg: 'bg-red-50 border-red-200'    },
  OTHER:         { icon: '⚠',  colour: 'text-gray-700',   bg: 'bg-gray-50 border-gray-200'  },
}

const SEVERITY_BADGE: Record<string, string> = {
  critical: 'bg-red-600 text-white',
  major:    'bg-orange-500 text-white',
  moderate: 'bg-amber-400 text-gray-900',
  minor:    'bg-blue-200 text-blue-800',
}

// ─── Component ────────────────────────────────────────────────────────────────

export default function DUROverrideModal({
  rxId, rxNumber, alert, drugName = '', onOverrideComplete, onCancel,
}: Props) {
  const staffId = localStorage.getItem('staff_id') || 'pharmacist'

  const [reasonCode,       setReasonCode]       = useState('')
  const [clinicalNotes,    setClinicalNotes]    = useState('')
  const [callbackDone,     setCallbackDone]     = useState(false)
  const [callbackNote,     setCallbackNote]     = useState('')
  const [submitError,      setSubmitError]      = useState<string | null>(null)

  const alertStyle = ALERT_STYLE[alert.alert_type] ?? ALERT_STYLE.OTHER

  // Load reason codes from API (with demo fallback)
  const { data: rawCodes } = useQuery({
    queryKey: ['dur-reason-codes'],
    queryFn:  () => apiClient.get('/dur/reason-codes').then(r => r.data.reason_codes as ReasonCode[]),
    staleTime: Infinity,
  })
  const reasonCodes = rawCodes ?? DEMO_REASON_CODES

  // ── #5 DUR Override Pattern Intelligence — AI reason suggestion ──
  const { data: suggestEnv } = useQuery({
    queryKey: ['dur-suggest', alert.alert_type, drugName],
    queryFn:  () => apiClient.get('/intelligence/dur/suggest', {
      params: { alert_type: alert.alert_type, drug_name: drugName, top_k: 3 },
    }).then(r => r.data as {
      result: { suggestions: ReasonSuggestion[]; top_reason_code: string; basis: string }
      tier_used: 'local' | 'cloud' | 'hybrid'; degraded: boolean; confidence: number
    }),
    staleTime: 60_000,
  })
  const suggestions   = suggestEnv?.result.suggestions ?? []
  const topSuggested  = suggestEnv?.result.top_reason_code ?? ''
  const suggestedSet  = new Set(suggestions.map(s => s.reason_code))

  // Auto-preselect the top AI suggestion (only while the field is still untouched).
  const [touched, setTouched] = useState(false)
  useEffect(() => {
    if (!touched && topSuggested && reasonCode === '') {
      setReasonCode(topSuggested)
    }
  }, [topSuggested]) // eslint-disable-line react-hooks/exhaustive-deps

  const overrideMutation = useMutation({
    mutationFn: () => apiClient.post('/dur/overrides', {
      rx_id:               rxId,
      alert_type:          alert.alert_type,
      alert_detail:        alert.alert_detail,
      reason_code:         reasonCode,
      clinical_notes:      clinicalNotes || undefined,
      prescriber_callback: callbackDone,
      callback_note:       callbackNote || undefined,
      overridden_by:       staffId,
    }).then(r => r.data as OverrideRecord),
    onSuccess: (data) => { onOverrideComplete(data.id) },
    onError:   (err: any) => {
      setSubmitError(err?.response?.data?.detail ?? 'Failed to save override')
    },
  })

  const canSubmit = reasonCode.length > 0 &&
    (reasonCode !== 'OTHER' || clinicalNotes.trim().length >= 5)

  return (
    <div className="fixed inset-0 bg-black/70 z-50 flex items-center justify-center p-4">
      <div className="bg-white rounded-xl shadow-2xl w-full max-w-lg max-h-[92vh] overflow-y-auto">

        {/* Header */}
        <div className="flex items-start justify-between px-5 py-4 border-b">
          <div>
            <h2 className="font-bold text-gray-900">DUR Alert Override</h2>
            <span className="text-xs text-gray-400 font-mono">{rxNumber}</span>
          </div>
          <button onClick={onCancel} className="text-gray-400 hover:text-gray-600 text-xl ml-4">×</button>
        </div>

        <div className="p-5 space-y-5">

          {/* Alert card */}
          <div className={`rounded-lg border px-4 py-3 ${alertStyle.bg}`}>
            <div className="flex items-start gap-3">
              <span className="text-2xl mt-0.5">{alertStyle.icon}</span>
              <div className="flex-1">
                <div className="flex items-center gap-2 mb-1">
                  <span className={`font-bold text-sm ${alertStyle.colour}`}>
                    {alert.alert_type.replace(/_/g, ' ')}
                  </span>
                  {alert.severity && (
                    <span className={`text-xs px-2 py-0.5 rounded-full font-semibold uppercase ${SEVERITY_BADGE[alert.severity]}`}>
                      {alert.severity}
                    </span>
                  )}
                </div>
                <p className={`text-sm leading-snug ${alertStyle.colour}`}>{alert.alert_detail}</p>
              </div>
            </div>
          </div>

          {/* Override reason */}
          <div>
            <div className="flex items-center gap-2 mb-2">
              <label className="text-xs font-semibold text-gray-700 uppercase tracking-wide">
                Override Reason <span className="text-red-500">*</span>
              </label>
              {suggestEnv && (
                <TierBadge tier={suggestEnv.tier_used} degraded={suggestEnv.degraded} compact />
              )}
            </div>

            {/* AI quick-pick chips — the top suggestions, one click to select */}
            {suggestions.length > 0 && (
              <div className="flex flex-wrap gap-1.5 mb-2">
                {suggestions.map((s, i) => (
                  <button
                    key={s.reason_code}
                    onClick={() => { setReasonCode(s.reason_code); setTouched(true) }}
                    title={`${Math.round(s.probability * 100)}% likely · ${s.support} past overrides (${s.basis})`}
                    className={`text-[11px] px-2 py-1 rounded-full border transition-all ${
                      reasonCode === s.reason_code
                        ? 'bg-purple-600 text-white border-purple-600'
                        : 'bg-purple-50 text-purple-700 border-purple-200 hover:bg-purple-100'
                    }`}
                  >
                    {i === 0 && '✨ '}{s.reason_label.split('—')[0].trim()}
                    <span className="opacity-60 ml-1">{Math.round(s.probability * 100)}%</span>
                  </button>
                ))}
              </div>
            )}

            <select
              value={reasonCode}
              onChange={e => { setReasonCode(e.target.value); setTouched(true) }}
              className="w-full border border-gray-200 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-purple-300"
            >
              <option value="">— Select a reason —</option>
              {reasonCodes.map(r => (
                <option key={r.code} value={r.code}>
                  {suggestedSet.has(r.code) ? '✨ ' : ''}{r.label}
                </option>
              ))}
            </select>
            {topSuggested && reasonCode === topSuggested && !touched && (
              <p className="text-[11px] text-purple-600 mt-1">
                ✨ AI pre-selected the most likely reason — change it if needed.
              </p>
            )}
          </div>

          {/* Clinical notes */}
          <div>
            <label className="text-xs font-semibold text-gray-700 uppercase tracking-wide block mb-1">
              Clinical Notes {reasonCode === 'OTHER' && <span className="text-red-500">*</span>}
            </label>
            <textarea
              value={clinicalNotes}
              onChange={e => setClinicalNotes(e.target.value)}
              rows={3}
              placeholder={
                reasonCode === 'OTHER'
                  ? 'Required: document the specific clinical circumstances…'
                  : 'Optional: document additional clinical context, monitoring plan…'
              }
              className="w-full border border-gray-200 rounded-lg px-3 py-2 text-sm resize-none focus:outline-none focus:ring-2 focus:ring-purple-300"
            />
          </div>

          {/* Prescriber callback */}
          <div className="border border-gray-100 rounded-lg p-3 space-y-2">
            <label className="flex items-center gap-2 cursor-pointer select-none">
              <input
                type="checkbox"
                checked={callbackDone}
                onChange={e => setCallbackDone(e.target.checked)}
                className="w-4 h-4 accent-purple-600"
              />
              <span className="text-sm font-medium text-gray-700">Prescriber callback performed</span>
            </label>
            {callbackDone && (
              <textarea
                value={callbackNote}
                onChange={e => setCallbackNote(e.target.value)}
                rows={2}
                placeholder="Time of call, prescriber name, instructions received…"
                className="w-full border border-gray-200 rounded-lg px-3 py-2 text-sm resize-none focus:outline-none focus:ring-2 focus:ring-purple-300"
              />
            )}
          </div>

          {/* Audit notice */}
          <div className="text-xs text-gray-400 bg-gray-50 rounded px-3 py-2 border border-gray-100">
            This override will be permanently recorded in the clinical audit log
            with your staff ID, timestamp, and the selected reason. This record
            may be reviewed during pharmacy inspections.
          </div>

          {submitError && (
            <p className="text-red-500 text-sm bg-red-50 border border-red-200 rounded px-3 py-2">
              ⚠ {submitError}
            </p>
          )}
        </div>

        {/* Footer */}
        <div className="flex gap-3 px-5 py-3 border-t bg-gray-50 rounded-b-xl sticky bottom-0">
          <button
            onClick={() => overrideMutation.mutate()}
            disabled={!canSubmit || overrideMutation.isPending}
            className="flex-1 px-4 py-2 bg-amber-600 text-white text-sm rounded-lg hover:bg-amber-700 disabled:opacity-40 font-semibold"
          >
            {overrideMutation.isPending
              ? '⌛ Saving…'
              : '✅ Record Override & Continue Dispensing'}
          </button>
          <button
            onClick={onCancel}
            className="px-4 py-2 text-gray-600 text-sm rounded-lg border border-gray-200 hover:bg-gray-100"
          >
            Cancel Rx
          </button>
        </div>
      </div>
    </div>
  )
}

// ─── Compact DUR Alert Banner ─────────────────────────────────────────────────
// Shown in the VerificationCenter before the modal is triggered

interface BannerProps {
  alerts:    DURAlert[]
  onOverride:(alert: DURAlert) => void
  onDismiss: () => void
}

export function DURAlertBanner({ alerts, onOverride, onDismiss }: BannerProps) {
  if (alerts.length === 0) return null

  const hasCritical = alerts.some(a => a.severity === 'critical')
  const first = alerts[0]
  const style = ALERT_STYLE[first.alert_type] ?? ALERT_STYLE.OTHER

  return (
    <div className={`rounded-lg border px-4 py-3 mb-3 ${style.bg}`}>
      <div className="flex items-start gap-2">
        <span className="text-xl">{style.icon}</span>
        <div className="flex-1 min-w-0">
          <p className={`font-semibold text-sm ${style.colour}`}>
            {alerts.length} DUR Alert{alerts.length > 1 ? 's' : ''}
            {hasCritical && <span className="ml-2 text-xs bg-red-600 text-white px-1.5 py-0.5 rounded">CRITICAL</span>}
          </p>
          <p className={`text-xs mt-0.5 truncate ${style.colour}`}>{first.alert_detail}</p>
          {alerts.length > 1 && (
            <p className={`text-xs opacity-70 ${style.colour}`}>
              +{alerts.length - 1} more alert{alerts.length - 1 > 1 ? 's' : ''}
            </p>
          )}
        </div>
        <div className="flex flex-col gap-1 ml-2">
          <button
            onClick={() => onOverride(first)}
            className="text-xs px-2 py-1 bg-amber-500 text-white rounded hover:bg-amber-600 whitespace-nowrap"
          >
            Override
          </button>
          <button
            onClick={onDismiss}
            className="text-xs px-2 py-1 border border-gray-300 text-gray-600 rounded hover:bg-gray-50"
          >
            Dismiss
          </button>
        </div>
      </div>
    </div>
  )
}
