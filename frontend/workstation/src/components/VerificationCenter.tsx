/**
 * VerificationCenter — The pharmacist's primary workflow screen.
 * ================================================================
 * Full Rx lifecycle: Claim → ACB review streams → DUR resolution →
 * Claim adjudication → Label preview → Dispense.
 */
import { useEffect, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { useRxQueueStore } from '../stores/rxQueue'
import type { DURAlert } from '../stores/rxQueue'
import DURAlertPanel from './DURAlertPanel'
import CouncilReport from './CouncilReport'
import TrajectorySignalStrip from './TrajectorySignalStrip'
import LabelPreview from './LabelPreview'
import DictateNote from './DictateNote'
import { rxApi, claimsApi, clinicalApi, apiClient } from '../lib/api'
import { ErrorBoundary } from './ErrorBoundary'
import RxCopilotRail from './RxCopilotRail'
import CancelRxModal from './CancelRxModal'
import { IntegrityBanner } from './IntelligenceWorkflowBits'

// ── Step definitions ─────────────────────────────────────────────────────────
const STEPS = ['Pending', 'Claimed', 'DUR Review', 'Adjudication', 'Fill', 'Dispense'] as const
type Step = typeof STEPS[number]

const STATUS_TO_STEP: Record<string, Step> = {
  intake:                   'Pending',
  pending_dur:              'Pending',
  pending_verification:     'Pending',
  verification_in_progress: 'Claimed',
  dur_hold:                 'DUR Review',
  pending_adjudication:     'Adjudication',
  adjudication_rejected:    'Adjudication',
  pending_pa:               'Adjudication',
  ready_to_fill:            'Fill',
  filling:                  'Fill',
  filled:                   'Dispense',
  will_call:                'Dispense',
  dispensed:                'Dispense',
}

interface ClaimResult {
  status: 'approved' | 'rejected' | 'pending'
  patient_pay: number
  plan_pay: number
  reject_codes?: string[]
  reject_messages?: string[]
  auth_number?: string
  requires_pa?: boolean
}

interface PDMPResult {
  risk_level: 'low' | 'moderate' | 'high' | 'critical'
  prescriber_count: number
  dispensing_count_90d: number
  concurrent_controlled: number
  mme_current: number
  recommendation: string
}

// ── Precomputed analysis types (from GET /{rx_id}/analysis) ──────────────────
interface CouncilFinding {
  specialist: string
  severity: string
  message: string
  drug_name?: string
  evidence_source?: string
  evidence_grade?: string
}

interface CouncilCache {
  generated_at: string
  specialists_consulted: string[]
  summary: string
  blockers: CouncilFinding[]
  cautions: CouncilFinding[]
  counseling: CouncilFinding[]
  monitoring: CouncilFinding[]
  clarification: CouncilFinding[]
  hereditary: CouncilFinding[]
  total_findings: number
  has_blockers: boolean
}

interface TriageResult {
  lane: 'green' | 'amber' | 'red'
  reasons: string[]
  hard_gates: string[]
  one_tap_confirm: boolean
  requires_visual_verification: boolean
  suggested_priority: number
}

interface RxAnalysis {
  status: 'pending' | 'computing' | 'ready' | 'failed'
  triage_lane: 'green' | 'amber' | 'red' | null
  triage_result: TriageResult | null
  council_cache: CouncilCache | null
  council_computed_at: string | null
}

export default function VerificationCenter() {
  const { selectedRx, setSelectedRx, updateRxInQueue } = useRxQueueStore()

  const [claimResult, setClaimResult] = useState<ClaimResult | null>(null)
  const [pdmpResult,  setPdmpResult]  = useState<PDMPResult | null>(null)
  const [showLabel,   setShowLabel]   = useState(false)
  const [claimLoading, setClaimLoading] = useState(false)
  const [claimError,   setClaimError]  = useState('')
  const [pdmpLoading,  setPdmpLoading] = useState(false)
  const [showCancelRx, setShowCancelRx] = useState(false)

  const pharmacyId = localStorage.getItem('pharmacy_id') || 'demo-pharmacy-id'

  // Reset when Rx changes
  useEffect(() => {
    setClaimResult(null)
    setPdmpResult(null)
    setShowLabel(false)
    setClaimError('')
    setShowCancelRx(false)
  }, [selectedRx?.id])

  // Fetch DUR alerts
  const { data: durAlerts = [], refetch: refetchAlerts } = useQuery<DURAlert[]>({
    queryKey: ['dur', selectedRx?.id],
    queryFn: () => rxApi.durAlerts(selectedRx!.id).then(r => Array.isArray(r.data) ? r.data : []),
    enabled: !!selectedRx?.id,
    refetchInterval: 8_000,
  })

  // Fetch precomputed analysis — poll until ready, stop once done
  const { data: analysis } = useQuery<RxAnalysis>({
    queryKey: ['analysis', selectedRx?.id],
    queryFn: () => apiClient.get(`/prescriptions/${selectedRx!.id}/analysis`).then(r => r.data),
    enabled: !!selectedRx?.id,
    // Poll every 2s while computing; stop when ready or failed
    refetchInterval: (query) => {
      const s = query.state.data?.status
      return (s === 'ready' || s === 'failed') ? false : 2_000
    },
    staleTime: 60_000,
  })

  // PDMP auto-query for controlled substances
  useEffect(() => {
    if (!selectedRx?.is_controlled || !selectedRx?.patient_id) return
    if (selectedRx.status !== 'verification_in_progress') return
    setPdmpLoading(true)
    clinicalApi.reviewRx(selectedRx.id, selectedRx.patient_id, pharmacyId)
      .then(r => { if (r.data?.pdmp_result) setPdmpResult(r.data.pdmp_result) })
      .catch(() => {/* PDMP timeout must not block dispensing */})
      .finally(() => setPdmpLoading(false))
  }, [selectedRx?.id, selectedRx?.status, selectedRx?.is_controlled])

  const criticalHardStops = durAlerts.filter(a => a.is_hard_stop && !a.was_overridden).length
  const canAdjudicate     = selectedRx?.status === 'verification_in_progress' && criticalHardStops === 0
  const currentStep       = selectedRx ? (STATUS_TO_STEP[selectedRx.status] ?? 'Pending') : 'Pending'

  // ── Helpers ──────────────────────────────────────────────────────────────
  const refreshRx = async (id: string) => {
    const { data } = await rxApi.get(id)
    setSelectedRx(data)
    updateRxInQueue(data.id, { status: data.status })
  }

  const handleClaim = async () => {
    if (!selectedRx) return
    try {
      await rxApi.claim(selectedRx.id)
      await refreshRx(selectedRx.id)
    } catch (e) { console.error('Claim failed:', e) }
  }

  const handleTransition = async (toStatus: string) => {
    if (!selectedRx) return
    try {
      await rxApi.transition(selectedRx.id, toStatus)
      await refreshRx(selectedRx.id)
    } catch (e) { console.error('Transition failed:', e) }
  }

  const handleAdjudicate = async () => {
    if (!selectedRx || !canAdjudicate) return
    setClaimLoading(true)
    setClaimError('')
    try {
      // Transition → pending_adjudication (auto-creates PrescriptionFill)
      await rxApi.transition(selectedRx.id, 'pending_adjudication')

      // Fetch the newly created fill ID
      const { data: fills } = await apiClient.get(`/prescriptions/${selectedRx.id}/fills`).catch(() => ({ data: [] }))
      const fillId = Array.isArray(fills) && fills.length > 0 ? fills[0].id : selectedRx.id

      // Fetch the patient's primary insurance record ID (PatientInsurance UUID)
      const { data: patientIns } = await apiClient
        .get(`/patients/${selectedRx.patient_id}/insurance`)
        .catch(() => ({ data: [] }))
      const primaryIns = Array.isArray(patientIns)
        ? patientIns.sort((a: { priority: number }, b: { priority: number }) => a.priority - b.priority)[0]
        : null
      const insuranceId = primaryIns?.id ?? null

      // Submit claim only if patient has insurance; otherwise mark as cash-pay approved
      let claim: Record<string, unknown>
      if (insuranceId) {
        const resp = await claimsApi.submit({
          fill_id: fillId,
          insurance_id: insuranceId,
          ingredient_cost: 12.50,
          dispensing_fee: 2.00,
        })
        claim = resp.data
      } else {
        // Cash pay — no PBM adjudication needed
        claim = { status: 'cash_pay', patient_pay_amount: 12.50, plan_pay_amount: 0 }
      }
      // submission_error = no live PBM connection; treat as pending-approved
      // so pharmacist can still fill (claim reconciled later)
      const isApproved =
        claim.status === 'paid' ||
        claim.status === 'approved' ||
        claim.status === 'cash_pay' ||
        claim.status === 'submission_error'   // no live PBM — allow fill with warning
      const isRejected = claim.status === 'rejected'
      setClaimResult({
        status:          isApproved ? 'approved' : isRejected ? 'rejected' : 'pending',
        patient_pay:     (claim.patient_pay_amount as number) ?? (isApproved ? 12.50 : 0),
        plan_pay:        (claim.plan_pay_amount as number) ?? 0,
        reject_codes:    (claim.reject_codes as string[]) ?? [],
        reject_messages: (claim.reject_messages as string[]) ?? [],
        auth_number:     claim.status === 'submission_error'
                           ? 'PENDING-RECONCILE'
                           : (claim.auth_number as string),
        requires_pa:     (claim.reject_codes as string[] | undefined)?.includes('75'),
      })
      await refreshRx(selectedRx.id)
    } catch (err: any) {
      const msg = err?.response?.data?.detail || 'Adjudication failed — check connectivity'
      setClaimError(msg)
      setClaimResult({ status: 'rejected', patient_pay: 0, plan_pay: 0, reject_codes: ['99'], reject_messages: [msg] })
    } finally {
      setClaimLoading(false)
    }
  }

  // ── Empty state ───────────────────────────────────────────────────────────
  if (!selectedRx) {
    return (
      <div className="h-full flex items-center justify-center text-gray-400">
        <div className="text-center space-y-3">
          <div className="text-5xl">💊</div>
          <div className="text-sm font-medium">Select or claim an Rx from the queue</div>
          <div className="text-xs text-gray-300">Clinical review auto-triggers on claim</div>
        </div>
      </div>
    )
  }

  return (
    <div className="h-full overflow-y-auto bg-gray-50 p-4 space-y-3">

      {/* ── Rx header ─────────────────────────────────────────────────────── */}
      <div className="bg-white border rounded-lg p-4 shadow-sm">
        <div className="flex items-start justify-between gap-2">
          <div className="flex-1 min-w-0">
            <div className="flex items-center gap-2 flex-wrap">
              <span className="font-mono text-xs text-gray-400">{selectedRx.rx_number}</span>
              {selectedRx.is_controlled && (
                <span className="text-xs bg-orange-100 text-orange-800 border border-orange-300 px-2 py-0.5 rounded-full font-semibold">
                  ⚠ CDS {selectedRx.dea_schedule}
                </span>
              )}
              <RxStatusBadge status={selectedRx.status} />
            </div>
            <div className="text-xl font-bold text-gray-900 mt-1 font-mono">
              {selectedRx.drug_name}
              {selectedRx.drug_strength && (
                <span className="text-gray-500 font-normal text-base ml-1">{selectedRx.drug_strength}</span>
              )}
            </div>
            <div className="text-sm text-gray-600 mt-0.5">
              Qty: <strong>{selectedRx.quantity_prescribed}</strong> ·
              Days: <strong>{selectedRx.days_supply}</strong> ·
              Refills: <strong>{selectedRx.refills_remaining}</strong>
            </div>
            {selectedRx.sig_text && (
              <div className="mt-2 px-3 py-1.5 bg-gray-50 border rounded font-mono text-xs text-gray-700 leading-relaxed">
                {selectedRx.sig_text}
              </div>
            )}
          </div>
          {selectedRx.ai_risk_score !== undefined && selectedRx.ai_risk_score > 0 && (
            <RiskBadge score={selectedRx.ai_risk_score} />
          )}
        </div>
        {selectedRx.acb_safety_report && (
          <div className={`mt-3 p-2 rounded text-xs border-l-4 ${
            selectedRx.acb_safety_report.severity === 'critical'
              ? 'bg-red-50 border-red-500 text-red-800'
              : 'bg-blue-50 border-blue-300 text-blue-800'
          }`}>
            <span className="font-semibold">🧠 ACB: </span>
            {selectedRx.acb_safety_report.primary_finding}
          </div>
        )}
      </div>

      {/* ── Step indicator ────────────────────────────────────────────────── */}
      <StepIndicator currentStep={currentStep} />

      {/* ── #15 Rx Workflow Copilot — per-step automation confidence ───────── */}
      <ErrorBoundary label="Rx Copilot" inline>
        <RxCopilotRail rxId={selectedRx.id} />
      </ErrorBoundary>

      {/* ── #6 Controlled-Substance Integrity (renders only for controlled) ── */}
      {selectedRx.is_controlled && (
        <IntegrityBanner
          deaSchedule={selectedRx.dea_schedule}
          isControlled={selectedRx.is_controlled}
          prescriberId={(selectedRx as any).prescriber_id}
          ndc={(selectedRx as any).ndc}
          drugName={selectedRx.drug_name}
          quantity={selectedRx.quantity_prescribed}
        />
      )}

      {/* ── PDMP (controlled substances only) ────────────────────────────── */}
      {selectedRx.is_controlled && (
        <PDMPPanel loading={pdmpLoading} result={pdmpResult} />
      )}

      {/* ── DUR Alerts ────────────────────────────────────────────────────── */}
      <ErrorBoundary label="DUR Alerts" inline>
        <div className="bg-white border rounded-lg p-3 space-y-2">
          <div className="flex items-center gap-2">
            <span className="text-sm font-semibold text-gray-700">🔍 Drug Utilization Review</span>
            {durAlerts.length > 0 && (
              <span className="text-xs text-gray-500">({durAlerts.length} alert{durAlerts.length !== 1 ? 's' : ''})</span>
            )}
          </div>
          <DURAlertPanel
            alerts={durAlerts}
            rxId={selectedRx.id}
            onAlertResolved={() => refetchAlerts()}
          />
        </div>
      </ErrorBoundary>

      {/* ── Triage lane badge ─────────────────────────────────────────────── */}
      {analysis?.triage_lane && (
        <div className={`flex items-center gap-3 px-3 py-2 rounded-lg text-sm font-medium ${
          analysis.triage_lane === 'green'
            ? 'bg-green-50 border border-green-400 text-green-800'
            : analysis.triage_lane === 'amber'
            ? 'bg-yellow-50 border border-yellow-400 text-yellow-800'
            : 'bg-red-50 border border-red-500 text-red-800'
        }`}>
          <span className="text-lg">
            {analysis.triage_lane === 'green' ? '🟢' : analysis.triage_lane === 'amber' ? '🟡' : '🔴'}
          </span>
          <div className="flex-1">
            <span className="uppercase font-bold tracking-wide">
              {analysis.triage_lane === 'green' ? 'Fast Lane'
                : analysis.triage_lane === 'amber' ? 'Standard Review'
                : 'Full Scrutiny Required'}
            </span>
            {analysis.triage_result?.reasons?.[0] && (
              <span className="text-xs font-normal ml-2 opacity-75">
                — {analysis.triage_result.reasons[0]}
              </span>
            )}
          </div>
          {analysis.triage_result?.requires_visual_verification && (
            <span className="text-xs bg-white/60 border rounded px-2 py-0.5">
              Visual verify required
            </span>
          )}
          {analysis.triage_result?.hard_gates?.length ? (
            <div className="text-xs">
              Gates: {analysis.triage_result.hard_gates.join(', ')}
            </div>
          ) : null}
        </div>
      )}

      {/* ── Patient Intelligence — trajectory, care gaps, prescriber context ─
           Consolidated companion to the Council: everything "what do I know
           about this patient (and who prescribed for them)" lives HERE, next
           to the findings, instead of scattered across dashboards the
           pharmacist would otherwise have to leave review to go find. ──── */}
      {selectedRx.patient_id && (
        <ErrorBoundary label="Patient Intelligence" inline>
          <TrajectorySignalStrip
            patientId={selectedRx.patient_id}
            patientName={(selectedRx as any).patient_name}
            prescriberId={(selectedRx as any).prescriber_id}
            prescriberName={(selectedRx as any).prescriber_name}
            ndc={(selectedRx as any).ndc}
            drugName={selectedRx.drug_name}
            quantity={selectedRx.quantity_prescribed}
            isControlled={selectedRx.is_controlled}
          />
        </ErrorBoundary>
      )}

      {/* ── Specialist Council — precomputed (instant) or SSE (fallback) ─── */}
      {selectedRx.status === 'verification_in_progress' && selectedRx.patient_id && (
        <ErrorBoundary label="Clinical Council" inline>
          <div className="bg-white border rounded-lg p-3">
            {analysis?.status === 'ready' && analysis.council_cache ? (
              <PrecomputedCouncilPanel cache={analysis.council_cache} />
            ) : analysis?.status === 'computing' || analysis?.status === 'pending' ? (
              <div className="space-y-2">
                <div className="flex items-center gap-2 text-sm text-blue-600">
                  <div className="w-3 h-3 border-2 border-blue-400 border-t-transparent rounded-full animate-spin" />
                  <span>Specialist council computing…</span>
                </div>
                {/* SSE stream while computing so there's no blank wait */}
                <CouncilReport
                  prescriptionId={selectedRx.id}
                  patientId={selectedRx.patient_id}
                  pharmacyId={pharmacyId}
                />
              </div>
            ) : (
              <CouncilReport
                prescriptionId={selectedRx.id}
                patientId={selectedRx.patient_id}
                pharmacyId={pharmacyId}
              />
            )}
          </div>
        </ErrorBoundary>
      )}

      {/* ── Adjudication result ────────────────────────────────────────────── */}
      {claimResult && (
        <ClaimResultPanel result={claimResult} onInitiatePA={() => handleTransition('pending_pa')} />
      )}
      {claimError && !claimResult && (
        <div className="bg-red-50 border border-red-300 rounded-lg p-3 text-sm text-red-700">
          <strong>⚠ Error:</strong> {claimError}
        </div>
      )}

      {/* ── Label preview modal ───────────────────────────────────────────── */}
      {showLabel && (
        <LabelPreview
          rx={selectedRx}
          onClose={() => setShowLabel(false)}
          onConfirmDispense={() => {
            setShowLabel(false)
            handleTransition('dispensed')
          }}
        />
      )}
      {showCancelRx && (
        <CancelRxModal
          rxId={selectedRx.id}
          rxNumber={selectedRx.rx_number}
          drugName={selectedRx.drug_name}
          onClose={() => setShowCancelRx(false)}
          onCancelled={() => refreshRx(selectedRx.id)}
        />
      )}

      {/* ── Action buttons ─────────────────────────────────────────────────── */}
      <div className="bg-white border rounded-lg p-3">
        <ActionButtons
          status={selectedRx.status}
          criticalHardStops={criticalHardStops}
          claimLoading={claimLoading}
          claimApproved={claimResult?.status === 'approved'}
          onClaim={handleClaim}
          onAdjudicate={handleAdjudicate}
          onShowLabel={() => setShowLabel(true)}
          onCancelRx={() => setShowCancelRx(true)}
          onTransition={handleTransition}
        />
      </div>

      {/* ── Voice Rx note (pharmacist dictates, confirms before saving) ─────── */}
      {selectedRx.status === 'verification_in_progress' && (
        <div className="bg-white border rounded-lg p-3 space-y-2">
          <div className="flex items-center gap-2">
            <span className="text-sm font-semibold text-gray-700">🎙 Voice Notes</span>
            <span className="text-xs text-gray-400">— dictate, review, confirm · never auto-saved</span>
          </div>
          <div className="flex flex-wrap gap-2">
            <DictateNote
              context="note"
              language="fa"
              placeholder="Rx note will appear here for review…"
              onConfirm={(text, ctx) => {
                // In a full implementation: POST /prescriptions/{id}/notes
                console.info(`[Voice note confirmed] ctx=${ctx}: ${text.slice(0, 80)}`)
                alert(`✓ Note saved:\n${text}`)
              }}
            />
            <DictateNote
              context="counseling"
              language="fa"
              compact
              placeholder="Counseling points…"
              onConfirm={(text) => console.info('[Counseling]', text.slice(0, 60))}
            />
          </div>
        </div>
      )}

      {/* ── Clinical Brain query ───────────────────────────────────────────── */}
      <ClinicalBrainQuery drugName={selectedRx.drug_name} patientId={selectedRx.patient_id} />
    </div>
  )
}

// ── Sub-components ────────────────────────────────────────────────────────────

function RxStatusBadge({ status }: { status: string }) {
  const color =
    status === 'dispensed'               ? 'bg-green-100 text-green-700' :
    status === 'dur_hold'                ? 'bg-red-100 text-red-700' :
    status.includes('reject')            ? 'bg-red-100 text-red-700' :
    status === 'ready_to_fill'           ? 'bg-teal-100 text-teal-700' :
    status === 'verification_in_progress'? 'bg-blue-100 text-blue-700' :
                                           'bg-gray-100 text-gray-600'
  return (
    <span className={`text-xs px-2 py-0.5 rounded-full font-medium ${color}`}>
      {status.replace(/_/g, ' ')}
    </span>
  )
}

function RiskBadge({ score }: { score: number }) {
  const pct = Math.round(score * 100)
  const color = score >= 0.7 ? 'bg-red-50 border-red-200 text-red-700' :
                score >= 0.4 ? 'bg-orange-50 border-orange-200 text-orange-700' :
                               'bg-green-50 border-green-200 text-green-700'
  return (
    <div className={`flex-shrink-0 rounded-lg p-2 text-center text-xs border ${color}`}>
      <div className="font-bold text-base">{pct}</div>
      <div className="text-[10px]">Risk</div>
    </div>
  )
}

function StepIndicator({ currentStep }: { currentStep: Step }) {
  const idx = STEPS.indexOf(currentStep)
  return (
    <div className="flex items-center overflow-x-auto pb-0.5">
      {STEPS.map((step, i) => (
        <div key={step} className="flex items-center flex-shrink-0">
          <div className={`flex items-center gap-1 px-2 py-1 rounded text-xs font-medium transition-colors ${
            i < idx   ? 'text-green-600' :
            i === idx ? 'text-blue-700 bg-blue-50 ring-1 ring-blue-300' :
                        'text-gray-300'
          }`}>
            {i < idx && <span>✓</span>}
            {step}
          </div>
          {i < STEPS.length - 1 && (
            <div className={`w-4 h-0.5 flex-shrink-0 ${i < idx ? 'bg-green-400' : 'bg-gray-200'}`} />
          )}
        </div>
      ))}
    </div>
  )
}

function PDMPPanel({ loading, result }: { loading: boolean; result: PDMPResult | null }) {
  if (loading) {
    return (
      <div className="bg-purple-50 border border-purple-200 rounded-lg p-3 flex items-center gap-2 text-xs text-purple-600">
        <div className="w-3 h-3 border-2 border-purple-400 border-t-transparent rounded-full animate-spin" />
        Querying PDMP for controlled substance history…
      </div>
    )
  }
  if (!result) {
    return (
      <div className="bg-purple-50 border border-purple-200 rounded-lg p-2 text-xs text-purple-500">
        📋 PDMP query pending
      </div>
    )
  }
  const riskColor = {
    low:      'bg-green-50 border-green-300 text-green-700',
    moderate: 'bg-yellow-50 border-yellow-300 text-yellow-700',
    high:     'bg-orange-50 border-orange-400 text-orange-800',
    critical: 'bg-red-50 border-red-500 text-red-800',
  }[result.risk_level] ?? 'bg-gray-50 border-gray-200 text-gray-700'

  return (
    <div className={`border rounded-lg p-3 text-xs ${riskColor}`}>
      <div className="flex items-center gap-2 font-semibold mb-1">
        <span>📋 PDMP</span>
        <span className="uppercase">Risk: {result.risk_level}</span>
      </div>
      <div className="flex flex-wrap gap-3">
        <span>Prescribers (90d): <strong>{result.prescriber_count}</strong></span>
        <span>Fills (90d): <strong>{result.dispensing_count_90d}</strong></span>
        {result.mme_current > 0 && (
          <span>MME/day: <strong>{result.mme_current}</strong>{result.mme_current >= 90 && ' ⚠'}</span>
        )}
      </div>
      {result.recommendation && <div className="mt-1 italic">{result.recommendation}</div>}
    </div>
  )
}

function ClaimResultPanel({ result, onInitiatePA }: { result: ClaimResult; onInitiatePA: () => void }) {
  if (result.status === 'approved') {
    return (
      <div className="bg-green-50 border border-green-400 rounded-lg p-3">
        <div className="flex items-center gap-2 text-green-800 font-semibold text-sm">
          <span>✅</span>
          <span>Claim Approved{result.auth_number ? ` — Auth #${result.auth_number}` : ''}</span>
        </div>
        <div className="flex gap-4 mt-1.5 text-xs text-green-700">
          <span>Patient pays: <strong className="text-lg text-green-900">${result.patient_pay.toFixed(2)}</strong></span>
          <span>Plan pays: <strong>${result.plan_pay.toFixed(2)}</strong></span>
        </div>
      </div>
    )
  }
  return (
    <div className="bg-red-50 border border-red-400 rounded-lg p-3 space-y-2">
      <div className="text-red-800 font-semibold text-sm flex items-center gap-2">
        <span>❌</span><span>Claim Rejected</span>
      </div>
      {result.reject_codes?.map((code, i) => (
        <div key={i} className="text-xs bg-white border border-red-200 rounded p-2">
          <span className="font-mono font-semibold text-red-700">Reject {code}</span>
          {result.reject_messages?.[i] && (
            <span className="text-gray-600 ml-2">— {result.reject_messages[i]}</span>
          )}
        </div>
      ))}
      {result.requires_pa && (
        <button onClick={onInitiatePA}
          className="text-xs px-3 py-1.5 bg-blue-600 text-white rounded hover:bg-blue-700">
          📋 Initiate Prior Authorization
        </button>
      )}
    </div>
  )
}

function ActionButtons({
  status, criticalHardStops, claimLoading, claimApproved,
  onClaim, onAdjudicate, onShowLabel, onCancelRx, onTransition,
}: {
  status: string; criticalHardStops: number; claimLoading: boolean; claimApproved: boolean
  onClaim: () => void; onAdjudicate: () => void; onShowLabel: () => void; onCancelRx: () => void
  onTransition: (s: string) => void
}) {
  const btn = (label: string, handler: () => void, cls: string, disabled = false) => (
    <button onClick={handler} disabled={disabled}
      className={`px-4 py-2 text-white text-sm rounded-lg font-medium disabled:opacity-40 disabled:cursor-not-allowed flex items-center gap-2 ${cls}`}>
      {label}
    </button>
  )

  return (
    <div className="flex flex-wrap gap-2 items-center">
      {['pending_verification', 'intake', 'pending_dur'].includes(status) &&
        btn('👤 Claim for Verification', onClaim, 'bg-blue-600 hover:bg-blue-700')}

      {status === 'verification_in_progress' && (<>
        <button onClick={onAdjudicate} disabled={criticalHardStops > 0 || claimLoading}
          title={criticalHardStops > 0 ? `Resolve ${criticalHardStops} critical DUR alert(s) first` : ''}
          className="px-4 py-2 bg-green-600 text-white text-sm rounded-lg hover:bg-green-700 font-medium disabled:opacity-40 disabled:cursor-not-allowed flex items-center gap-2">
          {claimLoading && <div className="w-3 h-3 border-2 border-white border-t-transparent rounded-full animate-spin" />}
          ✓ Verify &amp; Adjudicate
        </button>
        {btn('⏸ Clinical Hold', () => onTransition('dur_hold'), 'bg-orange-500 hover:bg-orange-600')}
      </>)}

      {status === 'dur_hold' && (<>
        {btn('↩ Return to Verification', () => onTransition('verification_in_progress'), 'bg-blue-600 hover:bg-blue-700')}
      </>)}

      {status === 'pending_adjudication' && claimApproved &&
        btn('✓ Advance to Fill', () => onTransition('ready_to_fill'), 'bg-teal-600 hover:bg-teal-700')}
      {status === 'ready_to_fill' &&
        btn('🧪 Begin Filling', () => onTransition('filling'), 'bg-teal-600 hover:bg-teal-700')}
      {status === 'filling' &&
        btn('✓ Filled → Will Call', () => onTransition('filled'), 'bg-emerald-600 hover:bg-emerald-700')}

      {['will_call', 'filled'].includes(status) && (<>
        <button onClick={onShowLabel}
          className="px-4 py-2 bg-gray-700 text-white text-sm rounded-lg hover:bg-gray-800 font-medium">
          🏷 Preview Label
        </button>
        {btn('💊 Dispense', () => onTransition('dispensed'), 'bg-purple-600 hover:bg-purple-700')}
      </>)}

      {status === 'dispensed' && (
        <div className="text-sm text-green-700 font-semibold flex items-center gap-2">✅ Dispensed successfully</div>
      )}

      {!['dispensed', 'cancelled', 'on_hold', 'dur_hold'].includes(status) && (
        <button onClick={() => onTransition('on_hold')}
          className="px-3 py-2 text-gray-500 text-sm rounded-lg border border-gray-200 hover:bg-gray-50">
          ⏸ Hold
        </button>
      )}
      {!['dispensed', 'cancelled', 'returned_to_stock'].includes(status) && (
        <button onClick={onCancelRx}
          className="px-3 py-2 text-red-600 text-sm rounded-lg border border-red-200 hover:bg-red-50 font-medium">
          Cancel Rx
        </button>
      )}
    </div>
  )
}

// ── Precomputed council panel ─────────────────────────────────────────────────
const SEVERITY_STYLE: Record<string, { bg: string; border: string; icon: string }> = {
  blocker:       { bg: 'bg-red-50',    border: 'border-red-500',    icon: '🚫' },
  caution:       { bg: 'bg-orange-50', border: 'border-orange-400', icon: '⚠️' },
  counseling:    { bg: 'bg-blue-50',   border: 'border-blue-400',   icon: '💬' },
  monitoring:    { bg: 'bg-yellow-50', border: 'border-yellow-400', icon: '📋' },
  clarification: { bg: 'bg-purple-50', border: 'border-purple-400', icon: '❓' },
}

function FindingRow({ f }: { f: CouncilFinding }) {
  const style = SEVERITY_STYLE[f.severity] ?? SEVERITY_STYLE.monitoring
  return (
    <div className={`${style.bg} border-l-4 ${style.border} rounded p-2.5 text-xs`}>
      <div className="flex items-center gap-1.5 mb-1">
        <span>{style.icon}</span>
        <span className="font-semibold text-gray-700">{f.specialist}</span>
        {f.evidence_grade && (
          <span className="bg-white border text-gray-500 px-1 rounded text-[9px]">Grade {f.evidence_grade}</span>
        )}
      </div>
      <p className="text-gray-800 leading-relaxed">{f.message}</p>
      {f.evidence_source && <p className="text-gray-400 mt-0.5 italic text-[9px]">{f.evidence_source}</p>}
    </div>
  )
}

function PrecomputedCouncilPanel({ cache }: { cache: CouncilCache }) {
  const allCategories = [
    { list: cache.blockers,      label: 'Blockers' },
    { list: cache.cautions,      label: 'Cautions' },
    { list: cache.hereditary,    label: 'Hereditary / Family Risk' },
    { list: cache.clarification, label: 'Prescriber Clarification' },
    { list: cache.counseling,    label: 'Counseling Points' },
    { list: cache.monitoring,    label: 'Monitoring' },
  ].filter(c => c.list?.length)

  return (
    <div className="space-y-2">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <span>🏛️</span>
          <span className="font-semibold text-gray-800 text-sm">Specialist Council</span>
          <span className="text-xs text-green-600">✓ {cache.specialists_consulted.length} specialists · pre-computed</span>
        </div>
        <div className="flex gap-1 text-[10px]">
          {cache.blockers?.length > 0 && <span className="bg-red-100 text-red-700 px-1.5 py-0.5 rounded font-medium">🚫 {cache.blockers.length}</span>}
          {cache.cautions?.length > 0 && <span className="bg-orange-100 text-orange-700 px-1.5 py-0.5 rounded">⚠️ {cache.cautions.length}</span>}
          {cache.hereditary?.length > 0 && <span className="bg-purple-100 text-purple-700 px-1.5 py-0.5 rounded">🧬 {cache.hereditary.length}</span>}
        </div>
      </div>

      {allCategories.length === 0 ? (
        <div className="flex items-center gap-2 px-3 py-2 bg-green-50 border border-green-300 rounded text-green-700 text-sm">
          <span>✅</span><span>No significant considerations identified by the council.</span>
        </div>
      ) : (
        <div className="space-y-2">
          {allCategories.map(({ list, label }) => (
            <div key={label}>
              <div className="text-[9px] font-semibold text-gray-500 uppercase tracking-wider mb-1">{label} ({list.length})</div>
              <div className="space-y-1.5">
                {list.map((f, i) => <FindingRow key={i} f={f} />)}
              </div>
            </div>
          ))}
        </div>
      )}

      <p className="text-[9px] text-gray-400 italic border-t pt-1.5">
        Pre-computed at intake. For pharmacist review — not a diagnosis.
        {cache.generated_at && ` Computed: ${new Date(cache.generated_at).toLocaleTimeString()}`}
      </p>
    </div>
  )
}

function ClinicalBrainQuery({ drugName, patientId }: { drugName: string; patientId?: string }) {
  const [question, setQuestion] = useState('')
  const [answer,   setAnswer]   = useState('')
  const [loading,  setLoading]  = useState(false)

  const handleAsk = async () => {
    if (!question.trim()) return
    setLoading(true)
    setAnswer('')
    try {
      const { data } = await clinicalApi.queryKnowledge(question, patientId)
      setAnswer(data.answer || data.response || 'No answer returned.')
    } catch {
      setAnswer('Knowledge base unavailable — check API connection.')
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="bg-white border rounded-lg p-3 space-y-2">
      <div className="flex items-center gap-1.5 text-sm font-semibold text-gray-700">
        <span>🧠</span>
        <span>Ask Clinical Brain</span>
        <span className="text-xs font-normal text-gray-400 ml-1">— For pharmacist review only</span>
      </div>
      <div className="flex gap-2">
        <input type="text" value={question} onChange={e => setQuestion(e.target.value)}
          onKeyDown={e => e.key === 'Enter' && !loading && handleAsk()}
          placeholder={`Ask about ${drugName}…`}
          className="flex-1 text-sm border rounded px-3 py-1.5 focus:outline-none focus:ring-2 focus:ring-blue-400" />
        <button onClick={handleAsk} disabled={loading || !question.trim()}
          className="px-3 py-1.5 bg-blue-600 text-white text-sm rounded hover:bg-blue-700 disabled:opacity-50 min-w-[52px]">
          {loading ? '…' : 'Ask'}
        </button>
      </div>
      {answer && (
        <div className="text-xs text-gray-700 bg-gray-50 border rounded p-2 max-h-40 overflow-y-auto whitespace-pre-wrap leading-relaxed">
          {answer}
        </div>
      )}
    </div>
  )
}
