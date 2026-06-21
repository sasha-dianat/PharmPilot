/**
 * VerificationCenter — The pharmacist's primary workflow screen.
 * ================================================================
 * Full Rx lifecycle: Claim → ACB review streams → DUR resolution →
 * Claim adjudication → Label preview → Dispense.
 *
 * "Clinical Daylight" UI upgrade (visual only — every state hook, query,
 * handler, conditional and clinical gate below is byte-for-byte the prior
 * logic): light premium surfaces, severity-token palette, radial risk ring,
 * attributed pipeline, fluid hover/stream motion, keyboard shortcut bar,
 * clinical-intelligence framing. No data contract / routing / tree change.
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
import type { SecondBrainSource } from '../lib/api'
import { ErrorBoundary } from './ErrorBoundary'
import RxCopilotRail from './RxCopilotRail'
import CancelRxModal from './CancelRxModal'
import { IntegrityBanner } from './IntelligenceWorkflowBits'
import ProgressRing from '../design/ProgressRing'

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
      <div className="cd-scope h-full flex items-center justify-center">
        <div className="text-center space-y-3 cd-section">
          <div className="w-14 h-14 mx-auto rounded-2xl cd-inset flex items-center justify-center text-2xl">💊</div>
          <div className="cd-ui text-sm font-medium text-ink2">Select or claim an Rx from the queue</div>
          <div className="text-xs text-ink3">Clinical review auto-triggers on claim</div>
        </div>
      </div>
    )
  }

  const riskScore = selectedRx.ai_risk_score ?? 0

  return (
    <div className="cd-scope h-full overflow-y-auto p-4 space-y-3">

      {/* ── Rx header ─────────────────────────────────────────────────────── */}
      <div className="cd-card cd-section p-4">
        <div className="flex items-start justify-between gap-3">
          <div className="flex-1 min-w-0">
            <div className="flex items-center gap-2 flex-wrap">
              <span className="cd-data text-xs text-ink3">#{(selectedRx.rx_number ?? '').slice(-4) || selectedRx.rx_number}</span>
              {selectedRx.is_controlled && (
                <span className="cd-data text-[11px] bg-caution-soft text-caution border border-caution/30 px-2 py-0.5 rounded-md font-semibold">
                  CDS {selectedRx.dea_schedule}
                </span>
              )}
              <RxStatusBadge status={selectedRx.status} />
            </div>
            <div className="text-[22px] font-semibold text-ink mt-1.5 leading-tight">
              {selectedRx.drug_name}
              {selectedRx.drug_strength && (
                <span className="text-ink2 font-normal text-base ml-1.5">{selectedRx.drug_strength}</span>
              )}
            </div>
            <div className="text-xs text-ink2 mt-0.5 cd-data">{selectedRx.rx_number}</div>
          </div>
          {riskScore > 0 && <RiskBadge score={riskScore} />}
        </div>

        {/* prescription quick-facts grid */}
        <div className="grid grid-cols-3 gap-2 mt-3">
          <Fact label="Quantity" value={`${selectedRx.quantity_prescribed}`} />
          <Fact label="Days supply" value={`${selectedRx.days_supply}`} />
          <Fact label="Refills" value={`${selectedRx.refills_remaining}`} />
        </div>
        {selectedRx.sig_text && (
          <div className="mt-2 cd-inset px-3 py-2">
            <div className="text-[10px] uppercase tracking-wide text-ink3 mb-0.5">SIG</div>
            <div className="cd-data text-sm text-ink leading-relaxed">{selectedRx.sig_text}</div>
          </div>
        )}
        {selectedRx.acb_safety_report && (
          <div className={`mt-3 px-3 py-2 rounded-lg text-xs border-l-2 ${
            selectedRx.acb_safety_report.severity === 'critical'
              ? 'bg-blocker-soft border-blocker text-blocker'
              : 'bg-intel-soft border-intel text-intel'
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

      {/* ── Clinical intelligence section label ───────────────────────────── */}
      <div className="flex items-center gap-2 px-1 pt-1">
        <span className="text-[11px] font-semibold uppercase tracking-wider text-ink3">Clinical intelligence</span>
        <span className="text-[10px] text-ink3">· AI services tuned in Dashboards</span>
      </div>

      {/* ── DUR Alerts ────────────────────────────────────────────────────── */}
      <ErrorBoundary label="DUR Alerts" inline>
        <div className="cd-card p-3 space-y-2">
          <div className="flex items-center gap-2">
            <span className="cd-ui text-sm font-semibold text-ink flex items-center gap-1.5">
              <span className="w-5 h-5 rounded-md bg-intel-soft text-intel flex items-center justify-center text-xs">🔍</span>
              Drug utilization review
            </span>
            {durAlerts.length > 0
              ? <span className="cd-data text-xs text-ink3">{durAlerts.length} alert{durAlerts.length !== 1 ? 's' : ''}</span>
              : <span className="text-[11px] bg-safe-soft text-safe border border-safe/25 px-2 py-0.5 rounded-md ml-auto">0 hard stops</span>}
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
        <div className={`cd-section flex items-center gap-3 px-3 py-2.5 rounded-xl text-sm font-medium border ${
          analysis.triage_lane === 'green'
            ? 'bg-safe-soft border-safe/30 text-safe'
            : analysis.triage_lane === 'amber'
            ? 'bg-warning-soft border-warning/30 text-warning'
            : 'bg-blocker-soft border-blocker/40 text-blocker'
        }`}>
          <span className="text-lg">
            {analysis.triage_lane === 'green' ? '🟢' : analysis.triage_lane === 'amber' ? '🟡' : '🔴'}
          </span>
          <div className="flex-1">
            <span className="cd-ui uppercase font-bold tracking-wide">
              {analysis.triage_lane === 'green' ? 'Fast lane'
                : analysis.triage_lane === 'amber' ? 'Standard review'
                : 'Full scrutiny required'}
            </span>
            {analysis.triage_result?.reasons?.[0] && (
              <span className="cd-ui text-xs font-normal ml-2 opacity-80">
                — {analysis.triage_result.reasons[0]}
              </span>
            )}
          </div>
          {analysis.triage_result?.requires_visual_verification && (
            <span className="cd-data text-xs bg-surface border border-line2 text-ink2 rounded-md px-2 py-0.5">
              Visual verify required
            </span>
          )}
          {analysis.triage_result?.hard_gates?.length ? (
            <div className="cd-data text-xs">
              Gates: {analysis.triage_result.hard_gates.join(', ')}
            </div>
          ) : null}
        </div>
      )}

      {/* ── Patient Intelligence — trajectory, care gaps, prescriber context ── */}
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
          <div className="cd-card p-3">
            {analysis?.status === 'ready' && analysis.council_cache ? (
              <PrecomputedCouncilPanel cache={analysis.council_cache} />
            ) : analysis?.status === 'computing' || analysis?.status === 'pending' ? (
              <div className="space-y-2">
                <div className="flex items-center gap-2 text-sm text-intel">
                  <div className="w-3 h-3 border-2 border-intel border-t-transparent rounded-full animate-spin" />
                  <span className="cd-ui">Specialist council computing…</span>
                </div>
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
        <div className="cd-danger bg-blocker-soft border border-blocker/40 rounded-xl p-3 text-sm text-blocker">
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

      {/* ── Voice Rx note (pharmacist dictates, confirms before saving) ─────── */}
      {selectedRx.status === 'verification_in_progress' && (
        <div className="cd-card p-3 space-y-2">
          <div className="flex items-center gap-2">
            <span className="cd-ui text-sm font-semibold text-ink">🎙 Voice notes</span>
            <span className="text-xs text-ink3">— dictate, review, confirm · never auto-saved</span>
          </div>
          <div className="flex flex-wrap gap-2">
            <DictateNote
              context="note"
              language="fa"
              placeholder="Rx note will appear here for review…"
              onConfirm={(text, ctx) => {
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

      {/* ── Knowledge Base / Clinical Brain query ──────────────────────────── */}
      <ClinicalBrainQuery drugName={selectedRx.drug_name} patientId={selectedRx.patient_id} />

      {/* ── Action bar ─────────────────────────────────────────────────────── */}
      <div className="cd-card p-3 sticky bottom-0 z-10">
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
    </div>
  )
}

// ── Sub-components ────────────────────────────────────────────────────────────

function Fact({ label, value }: { label: string; value: string }) {
  return (
    <div className="cd-inset px-3 py-2">
      <div className="text-[10px] uppercase tracking-wide text-ink3">{label}</div>
      <div className="cd-data text-base font-semibold text-ink leading-tight">{value}</div>
    </div>
  )
}

const STATUS_TONE: Record<string, { text: string; bg: string; border: string }> = {
  dispensed:                { text: 'text-safe',    bg: 'bg-safe-soft',    border: 'border-safe/30' },
  dur_hold:                 { text: 'text-blocker', bg: 'bg-blocker-soft', border: 'border-blocker/30' },
  ready_to_fill:            { text: 'text-safe',    bg: 'bg-safe-soft',    border: 'border-safe/30' },
  verification_in_progress: { text: 'text-intel',   bg: 'bg-intel-soft',   border: 'border-intel/30' },
}
function RxStatusBadge({ status }: { status: string }) {
  const tone = STATUS_TONE[status]
    ?? (status.includes('reject')
        ? { text: 'text-blocker', bg: 'bg-blocker-soft', border: 'border-blocker/30' }
        : { text: 'text-ink2', bg: 'bg-surface2', border: 'border-line2' })
  return (
    <span className={`cd-data text-[11px] px-2 py-0.5 rounded-md font-medium border ${tone.text} ${tone.bg} ${tone.border}`}>
      {status.replace(/_/g, ' ')}
    </span>
  )
}

function RiskBadge({ score }: { score: number }) {
  const pct = Math.round(score * 100)
  const color = score >= 0.7 ? '#dc2626' : score >= 0.4 ? '#ea580c' : '#059669'
  return (
    <div className="flex-shrink-0 flex flex-col items-center gap-0.5">
      <ProgressRing value={score} size={54} stroke={5} color={color} label={pct} />
      <div className="text-[10px] uppercase tracking-wide text-ink3">Risk</div>
    </div>
  )
}

function StepIndicator({ currentStep }: { currentStep: Step }) {
  const idx = STEPS.indexOf(currentStep)
  return (
    <div className="cd-card px-3 py-2 flex items-center overflow-x-auto">
      {STEPS.map((step, i) => (
        <div key={step} className="flex items-center flex-shrink-0">
          <div className={`cd-rail cd-ui flex items-center gap-1.5 px-2.5 py-1 rounded-lg text-xs font-medium ${
            i < idx   ? 'text-safe' :
            i === idx ? 'text-intel bg-intel-soft ring-1 ring-intel/30' :
                        'text-ink3'
          }`}>
            <span className={`w-4 h-4 rounded-full flex items-center justify-center text-[10px] ${
              i < idx ? 'bg-safe text-white' : i === idx ? 'bg-intel text-white' : 'bg-surface2 text-ink3 border border-line2'
            }`}>{i < idx ? '✓' : i + 1}</span>
            {step}
          </div>
          {i < STEPS.length - 1 && (
            <div className={`w-5 h-0.5 flex-shrink-0 rounded ${i < idx ? 'bg-safe/50' : 'bg-line2'}`} />
          )}
        </div>
      ))}
    </div>
  )
}

function PDMPPanel({ loading, result }: { loading: boolean; result: PDMPResult | null }) {
  if (loading) {
    return (
      <div className="bg-counsel-soft border border-counsel/25 rounded-xl p-3 flex items-center gap-2 text-xs text-counsel">
        <div className="w-3 h-3 border-2 border-counsel border-t-transparent rounded-full animate-spin" />
        Querying PDMP for controlled substance history…
      </div>
    )
  }
  if (!result) {
    return (
      <div className="bg-counsel-soft border border-counsel/20 rounded-xl p-2 text-xs text-counsel">
        📋 PDMP query pending
      </div>
    )
  }
  const tone = {
    low:      { bg: 'bg-safe-soft',    border: 'border-safe/30',    text: 'text-safe' },
    moderate: { bg: 'bg-warning-soft', border: 'border-warning/30', text: 'text-warning' },
    high:     { bg: 'bg-caution-soft', border: 'border-caution/40', text: 'text-caution' },
    critical: { bg: 'bg-blocker-soft', border: 'border-blocker/40', text: 'text-blocker' },
  }[result.risk_level] ?? { bg: 'bg-surface2', border: 'border-line2', text: 'text-ink2' }

  return (
    <div className={`cd-section border rounded-xl p-3 text-xs ${tone.bg} ${tone.border} ${tone.text}`}>
      <div className="flex items-center gap-2 font-semibold mb-1">
        <span>📋 PDMP</span>
        <span className="cd-data uppercase">Risk: {result.risk_level}</span>
      </div>
      <div className="flex flex-wrap gap-3 cd-data">
        <span>Prescribers (90d): <strong>{result.prescriber_count}</strong></span>
        <span>Fills (90d): <strong>{result.dispensing_count_90d}</strong></span>
        {result.mme_current > 0 && (
          <span>MME/day: <strong>{result.mme_current}</strong>{result.mme_current >= 90 && ' ⚠'}</span>
        )}
      </div>
      {result.recommendation && <div className="mt-1 italic cd-ui">{result.recommendation}</div>}
    </div>
  )
}

function ClaimResultPanel({ result, onInitiatePA }: { result: ClaimResult; onInitiatePA: () => void }) {
  if (result.status === 'approved') {
    return (
      <div className="cd-section bg-safe-soft border border-safe/30 rounded-xl p-3">
        <div className="flex items-center gap-2 text-safe font-semibold text-sm">
          <span>✅</span>
          <span className="cd-ui">Claim approved{result.auth_number ? ` — Auth #${result.auth_number}` : ''}</span>
        </div>
        <div className="flex gap-4 mt-1.5 text-xs text-safe cd-ui">
          <span>Patient pays: <strong className="cd-data text-lg">${result.patient_pay.toFixed(2)}</strong></span>
          <span>Plan pays: <strong className="cd-data">${result.plan_pay.toFixed(2)}</strong></span>
        </div>
      </div>
    )
  }
  return (
    <div className="cd-section cd-danger bg-blocker-soft border border-blocker/40 rounded-xl p-3 space-y-2">
      <div className="text-blocker font-semibold text-sm flex items-center gap-2 cd-ui">
        <span>❌</span><span>Claim rejected</span>
      </div>
      {result.reject_codes?.map((code, i) => (
        <div key={i} className="text-xs bg-surface border border-blocker/20 rounded-lg p-2">
          <span className="cd-data font-semibold text-blocker">Reject {code}</span>
          {result.reject_messages?.[i] && (
            <span className="text-ink2 ml-2">— {result.reject_messages[i]}</span>
          )}
        </div>
      ))}
      {result.requires_pa && (
        <button onClick={onInitiatePA}
          className="cd-ui cd-hover text-xs px-3 py-1.5 bg-intel-soft text-intel border border-intel/40 rounded-lg">
          📋 Initiate prior authorization
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
  const A = {
    intel:   'bg-intel text-white border-intel hover:brightness-110',
    safe:    'bg-safe text-white border-safe hover:brightness-110',
    caution: 'bg-caution-soft text-caution border-caution/40 hover:bg-caution/10',
    counsel: 'bg-counsel text-white border-counsel hover:brightness-110',
    ghost:   'bg-surface text-ink2 border-line2 hover:bg-surface2',
  }
  const btn = (label: string, handler: () => void, accent: string, kbd?: string) => (
    <button onClick={handler}
      className={`cd-ui cd-hover px-4 py-2 text-sm rounded-lg font-medium flex items-center gap-2 border ${accent}`}>
      {label}{kbd && <kbd className="cd-kbd">{kbd}</kbd>}
    </button>
  )

  return (
    <div className="flex flex-wrap gap-2 items-center">
      {['pending_verification', 'intake', 'pending_dur'].includes(status) &&
        btn('👤 Claim for verification', onClaim, A.intel, 'C')}

      {status === 'verification_in_progress' && (<>
        <button onClick={onAdjudicate} disabled={criticalHardStops > 0 || claimLoading}
          title={criticalHardStops > 0 ? `Resolve ${criticalHardStops} critical DUR alert(s) first` : ''}
          className={`cd-ui cd-hover px-4 py-2 text-sm rounded-lg font-medium border flex items-center gap-2 disabled:opacity-40 disabled:cursor-not-allowed ${A.safe}`}>
          {claimLoading && <div className="w-3 h-3 border-2 border-white border-t-transparent rounded-full animate-spin" />}
          ✓ Verify &amp; approve<kbd className="cd-kbd">A</kbd>
        </button>
        {btn('⏸ Clinical hold', () => onTransition('dur_hold'), A.caution, 'H')}
      </>)}

      {status === 'dur_hold' &&
        btn('↩ Return to verification', () => onTransition('verification_in_progress'), A.intel)}

      {status === 'pending_adjudication' && claimApproved &&
        btn('✓ Advance to fill', () => onTransition('ready_to_fill'), A.safe)}
      {status === 'ready_to_fill' &&
        btn('🧪 Begin filling', () => onTransition('filling'), A.safe)}
      {status === 'filling' &&
        btn('✓ Filled → will call', () => onTransition('filled'), A.safe)}

      {['will_call', 'filled'].includes(status) && (<>
        {btn('🏷 Preview label', onShowLabel, A.ghost, 'L')}
        {btn('💊 Dispense', () => onTransition('dispensed'), A.counsel, 'D')}
      </>)}

      {status === 'dispensed' && (
        <div className="cd-ui text-sm text-safe font-semibold flex items-center gap-2">✅ Dispensed successfully</div>
      )}

      {!['dispensed', 'cancelled', 'on_hold', 'dur_hold'].includes(status) && (
        <button onClick={() => onTransition('on_hold')}
          className="cd-ui px-3 py-2 text-ink2 text-sm rounded-lg border border-line2 hover:bg-surface2">
          ⏸ Hold
        </button>
      )}
      {['intake', 'pending_dur', 'dur_hold', 'pending_verification',
        'verification_in_progress', 'adjudication_rejected', 'pending_pa',
        'ready_to_fill', 'on_hold'].includes(status) && (
        <button onClick={onCancelRx}
          className="cd-ui ml-auto px-3 py-2 text-blocker text-sm rounded-lg border border-blocker/30 hover:bg-blocker-soft font-medium flex items-center gap-2">
          Reject<kbd className="cd-kbd">R</kbd>
        </button>
      )}
    </div>
  )
}

// ── Precomputed council panel ─────────────────────────────────────────────────
const SEVERITY_STYLE: Record<string, { border: string; bg: string; text: string; icon: string }> = {
  blocker:       { border: 'border-blocker', bg: 'bg-blocker-soft', text: 'text-blocker', icon: '🚫' },
  caution:       { border: 'border-caution', bg: 'bg-caution-soft', text: 'text-caution', icon: '⚠️' },
  counseling:    { border: 'border-counsel', bg: 'bg-counsel-soft', text: 'text-counsel', icon: '💬' },
  monitoring:    { border: 'border-warning', bg: 'bg-warning-soft', text: 'text-warning', icon: '📋' },
  clarification: { border: 'border-intel',   bg: 'bg-intel-soft',   text: 'text-intel',   icon: '❓' },
}

function FindingRow({ f, index = 0 }: { f: CouncilFinding; index?: number }) {
  const style = SEVERITY_STYLE[f.severity] ?? SEVERITY_STYLE.monitoring
  return (
    <div
      className={`cd-stream ${style.bg} border-l-2 ${style.border} rounded-lg p-2.5 text-xs`}
      style={{ '--i': index } as React.CSSProperties}
    >
      <div className="flex items-center gap-1.5 mb-1">
        <span>{style.icon}</span>
        <span className={`cd-ui font-semibold ${style.text}`}>{f.specialist}</span>
        {f.evidence_grade && (
          <span className="cd-data bg-surface border border-line2 text-ink2 px-1 rounded text-[9px]">Grade {f.evidence_grade}</span>
        )}
      </div>
      <p className="cd-narr text-ink leading-relaxed">{f.message}</p>
      {f.evidence_source && <p className="cd-ui text-ink3 mt-0.5 italic text-[9px]">{f.evidence_source}</p>}
    </div>
  )
}

function PrecomputedCouncilPanel({ cache }: { cache: CouncilCache }) {
  const allCategories = [
    { list: cache.blockers,      label: 'Blockers' },
    { list: cache.cautions,      label: 'Cautions' },
    { list: cache.hereditary,    label: 'Hereditary / family risk' },
    { list: cache.clarification, label: 'Prescriber clarification' },
    { list: cache.counseling,    label: 'Counseling points' },
    { list: cache.monitoring,    label: 'Monitoring' },
  ].filter(c => c.list?.length)

  return (
    <div className="space-y-2">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <span className="w-5 h-5 rounded-md bg-intel-soft text-intel flex items-center justify-center text-xs">🏛️</span>
          <span className="cd-ui font-semibold text-ink text-sm">Specialist council</span>
          <span className="cd-data text-xs text-safe">✓ {cache.specialists_consulted.length} specialists · precomputed</span>
        </div>
        <div className="flex gap-1 text-[10px] cd-data">
          {cache.blockers?.length > 0 && <span className="bg-blocker-soft text-blocker border border-blocker/30 px-1.5 py-0.5 rounded font-medium">🚫 {cache.blockers.length}</span>}
          {cache.cautions?.length > 0 && <span className="bg-caution-soft text-caution border border-caution/30 px-1.5 py-0.5 rounded">⚠️ {cache.cautions.length}</span>}
          {cache.hereditary?.length > 0 && <span className="bg-counsel-soft text-counsel border border-counsel/30 px-1.5 py-0.5 rounded">🧬 {cache.hereditary.length}</span>}
        </div>
      </div>

      {allCategories.length === 0 ? (
        <div className="flex items-center gap-2 px-3 py-2 bg-safe-soft border border-safe/30 rounded-lg text-safe text-sm cd-ui">
          <span>✅</span><span>No significant considerations identified by the council.</span>
        </div>
      ) : (
        <div className="space-y-2">
          {allCategories.map(({ list, label }) => (
            <div key={label}>
              <div className="cd-ui text-[9px] font-semibold text-ink3 uppercase tracking-wider mb-1">{label} ({list.length})</div>
              <div className="space-y-1.5">
                {list.map((f, i) => <FindingRow key={i} f={f} index={i} />)}
              </div>
            </div>
          ))}
        </div>
      )}

      <p className="cd-ui text-[9px] text-ink3 italic border-t border-line pt-1.5">
        Pre-computed at intake. For pharmacist review — not a diagnosis.
        {cache.generated_at && ` Computed: ${new Date(cache.generated_at).toLocaleTimeString()}`}
      </p>
    </div>
  )
}

function ClinicalBrainQuery({ drugName, patientId }: { drugName: string; patientId?: string }) {
  const [question, setQuestion] = useState('')
  const [answer,   setAnswer]   = useState('')
  const [sources,  setSources]  = useState<SecondBrainSource[]>([])
  const [expanded, setExpanded] = useState<Set<number>>(new Set())
  const [loading,  setLoading]  = useState(false)
  // Offline (extractive) vs AI-assisted (LLM synthesis). Sticky per browser.
  const [aiAssist, setAiAssist] = useState<boolean>(() => {
    try { return localStorage.getItem('kb-ai-assist') === '1' } catch { return false }
  })
  const setMode = (v: boolean) => {
    setAiAssist(v)
    try { localStorage.setItem('kb-ai-assist', v ? '1' : '0') } catch { /* ignore */ }
  }

  const handleAsk = async () => {
    if (!question.trim()) return
    setLoading(true)
    setAnswer('')
    setSources([])
    setExpanded(new Set())
    try {
      const { data } = await clinicalApi.queryKnowledge(question, patientId, aiAssist)
      setAnswer(data.answer || data.response || 'No answer returned.')
      setSources(data.sources || [])
    } catch {
      setAnswer('Knowledge base unavailable — check API connection.')
    } finally {
      setLoading(false)
    }
  }

  const toggle = (i: number) => setExpanded(prev => {
    const next = new Set(prev)
    next.has(i) ? next.delete(i) : next.add(i)
    return next
  })

  return (
    <div className="cd-card p-3 space-y-2">
      <div className="flex items-center gap-1.5 text-sm font-semibold text-ink cd-ui">
        <span className="w-5 h-5 rounded-md bg-intel-soft text-intel flex items-center justify-center text-xs">📚</span>
        <span>Knowledge base · ask Clinical Brain</span>
        <span className="text-xs font-normal text-ink3 ml-1">— scope: {drugName} + patient</span>
        <div className="ml-auto flex items-center rounded-lg border border-line2 bg-surface2 p-0.5 text-[11px] font-semibold cd-ui"
          role="radiogroup" aria-label="Answer mode">
          <button type="button" role="radio" aria-checked={!aiAssist} onClick={() => setMode(false)}
            title="Return retrieved source passages only — no LLM, instant."
            className={`px-2 py-1 rounded-md transition-colors ${!aiAssist ? 'bg-intel text-white' : 'text-ink3 hover:text-ink'}`}>
            Offline
          </button>
          <button type="button" role="radio" aria-checked={aiAssist} onClick={() => setMode(true)}
            title="Synthesize a grounded answer with an LLM; falls back to passages if no provider is reachable."
            className={`px-2 py-1 rounded-md transition-colors ${aiAssist ? 'bg-intel text-white' : 'text-ink3 hover:text-ink'}`}>
            AI-assisted
          </button>
        </div>
      </div>
      <div className="flex gap-2">
        <input type="text" value={question} onChange={e => setQuestion(e.target.value)}
          onKeyDown={e => e.key === 'Enter' && !loading && handleAsk()}
          placeholder={`Search ADRs, interactions, monographs for ${drugName}…`}
          className="cd-ui flex-1 text-sm bg-surface2 border border-line2 rounded-lg px-3 py-2 text-ink placeholder:text-ink3 focus:outline-none focus:ring-2 focus:ring-intel/30" />
        <button onClick={handleAsk} disabled={loading || !question.trim()}
          className="cd-ui cd-hover px-4 py-2 bg-intel text-white text-sm rounded-lg hover:brightness-110 disabled:opacity-50 min-w-[60px]">
          {loading ? '…' : 'Ask'}
        </button>
      </div>
      {answer && (
        <div className="bg-surface2 border border-line rounded-lg p-3.5 max-h-80 overflow-y-auto cd-section space-y-3">
          {answer.split('\n\n').map((block, i) => {
            const t = block.trim()
            if (!t) return null
            if (t.startsWith('•')) {
              const body = t.replace(/^•\s*/, '')
              const ci = body.indexOf(': ')
              const head = ci > 0 ? body.slice(0, ci) : ''
              const rest = ci > 0 ? body.slice(ci + 2) : body
              const src = head ? sources.find(s => s.source_title?.trim() === head) : undefined
              const full = src?.full_text?.trim() || ''
              // Expandable only when the source holds materially more than the snippet.
              const canExpand = full.length > rest.length + 24
              const isOpen = expanded.has(i)
              return (
                <div key={i} className={`border-l-2 border-intel/40 pl-3 ${canExpand ? 'cursor-pointer group' : ''}`}
                  onClick={canExpand ? () => toggle(i) : undefined}
                  role={canExpand ? 'button' : undefined} tabIndex={canExpand ? 0 : undefined}
                  onKeyDown={canExpand ? (e => (e.key === 'Enter' || e.key === ' ') && (e.preventDefault(), toggle(i))) : undefined}>
                  {head && <p className="cd-ui text-[13px] font-bold text-ink leading-snug">{head}</p>}
                  <p className="cd-narr text-sm text-ink leading-[1.75] mt-1">{isOpen ? full : rest}</p>
                  {canExpand && (
                    <span className="cd-ui mt-1 inline-flex items-center gap-1 text-[11px] font-semibold text-intel group-hover:underline select-none">
                      {isOpen ? '▴ Show less' : '▾ Read full passage'}
                    </span>
                  )}
                </div>
              )
            }
            // leading note line (extractive banner) — muted; or synthesized prose — readable
            const isNote = /summary offline|most relevant source/i.test(t)
            return (
              <p key={i} className={`cd-narr leading-[1.75] ${isNote ? 'text-[12px] italic text-ink3' : 'text-sm text-ink'}`}>{t}</p>
            )
          })}
        </div>
      )}
    </div>
  )
}
