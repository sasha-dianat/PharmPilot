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
import LabelPreview from './LabelPreview'
import { rxApi, claimsApi, clinicalApi, apiClient } from '../lib/api'
import { ErrorBoundary } from './ErrorBoundary'

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

export default function VerificationCenter() {
  const { selectedRx, setSelectedRx, updateRxInQueue } = useRxQueueStore()

  const [claimResult, setClaimResult] = useState<ClaimResult | null>(null)
  const [pdmpResult,  setPdmpResult]  = useState<PDMPResult | null>(null)
  const [showLabel,   setShowLabel]   = useState(false)
  const [claimLoading, setClaimLoading] = useState(false)
  const [claimError,   setClaimError]  = useState('')
  const [pdmpLoading,  setPdmpLoading] = useState(false)

  const pharmacyId = localStorage.getItem('pharmacy_id') || 'demo-pharmacy-id'

  // Reset when Rx changes
  useEffect(() => {
    setClaimResult(null)
    setPdmpResult(null)
    setShowLabel(false)
    setClaimError('')
  }, [selectedRx?.id])

  // Fetch DUR alerts
  const { data: durAlerts = [], refetch: refetchAlerts } = useQuery<DURAlert[]>({
    queryKey: ['dur', selectedRx?.id],
    queryFn: () => rxApi.durAlerts(selectedRx!.id).then(r => Array.isArray(r.data) ? r.data : []),
    enabled: !!selectedRx?.id,
    refetchInterval: 8_000,
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
      const { data: transitionData } = await rxApi.transition(selectedRx.id, 'pending_adjudication')
      const updatedRx = transitionData?.rx ?? transitionData

      // Fetch the newly created fill ID
      const { data: fills } = await apiClient.get(`/prescriptions/${selectedRx.id}/fills`).catch(() => ({ data: [] }))
      const fillId = Array.isArray(fills) && fills.length > 0 ? fills[0].id : selectedRx.id

      // Fetch primary insurance plan ID
      const { data: insPlans } = await apiClient.get('/insurance-plans?limit=1').catch(() => ({ data: [] }))
      const insuranceId = Array.isArray(insPlans) && insPlans.length > 0
        ? insPlans[0].id
        : '00000000-0000-0000-0000-000000000000'

      const { data: claim } = await claimsApi.submit({
        fill_id: fillId,
        insurance_id: insuranceId,
        ingredient_cost: 12.50,
        dispensing_fee: 2.00,
      })
      setClaimResult({
        status:          claim.status === 'paid' ? 'approved' : claim.status === 'rejected' ? 'rejected' : 'pending',
        patient_pay:     claim.patient_pay_amount ?? 0,
        plan_pay:        claim.plan_pay_amount ?? 0,
        reject_codes:    claim.reject_codes ?? [],
        reject_messages: claim.reject_messages ?? [],
        auth_number:     claim.auth_number,
        requires_pa:     claim.reject_codes?.includes('75'),
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

      {/* ── Specialist Council (SSE streaming) ───────────────────────────── */}
      {selectedRx.status === 'verification_in_progress' && selectedRx.patient_id && (
        <ErrorBoundary label="Clinical Council" inline>
          <div className="bg-white border rounded-lg p-3">
            <CouncilReport
              prescriptionId={selectedRx.id}
              patientId={selectedRx.patient_id}
              pharmacyId={pharmacyId}
            />
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
          onTransition={handleTransition}
        />
      </div>

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
  onClaim, onAdjudicate, onShowLabel, onTransition,
}: {
  status: string; criticalHardStops: number; claimLoading: boolean; claimApproved: boolean
  onClaim: () => void; onAdjudicate: () => void; onShowLabel: () => void
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
        <button onClick={() => onTransition('cancelled')}
          className="px-4 py-2 bg-red-100 text-red-700 text-sm rounded-lg hover:bg-red-200 border border-red-300 font-medium">
          Cancel Rx
        </button>
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
