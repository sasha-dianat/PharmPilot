/**
 * PatientPanel — Full patient profile in the right sidebar.
 * =========================================================
 * Replaces the basic PatientPanel in App.tsx with a rich profile:
 *   • Demographics + biometric status
 *   • Allergies (red badges — prominent)
 *   • Insurance cards (BIN/PCN)
 *   • Active medications list
 *   • Recent fills (last 5)
 *   • Lab values (eGFR highlighted for renal dosing)
 *   • Adherence risk score
 *   • Pending audio enrichment actions
 */
import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { patientApi, rxApi } from '../lib/api'
import { formatJalali, ageFromDob, dateDisplay } from '../lib/jalali'
import DictateNote from './DictateNote'

interface Props {
  patientId: string
}

interface LabResult {
  test_name: string
  value: string
  unit: string
  reference_range?: string
  collected_at: string
  is_critical: boolean
}

// Both "active meds" and "recent fills" are derived from the patient's Rx
// history (GET /prescriptions?patient_id=…), which already returns this shape
// per row — see rx_to_dict_from_row in services/platform/routers/prescriptions.py.
interface PatientRx {
  id: string
  rx_number: string
  drug_name: string
  drug_strength?: string | null
  sig_text: string
  status: string
  fill_date?: string | null
  days_supply: number
  is_controlled: boolean
  dea_schedule?: string | null
  prescriber_name?: string | null
  created_at?: string
}

const DISPENSE_TERMINAL_STATUSES = new Set(['dispensed', 'cancelled', 'returned_to_stock', 'transferred_out'])

// ── Lab flag for eGFR (renal dosing) ─────────────────────────────────────────
function getEGFRFlag(value: string): string | null {
  const v = parseFloat(value)
  if (isNaN(v)) return null
  if (v < 15)  return 'Stage 5 — Kidney Failure'
  if (v < 30)  return 'Stage 4 — Severe CKD ⚠'
  if (v < 45)  return 'Stage 3b — Moderate CKD'
  if (v < 60)  return 'Stage 3a — Mild-Moderate CKD'
  return null
}

export default function PatientPanel({ patientId }: Props) {
  const [activeTab, setActiveTab] = useState<'overview' | 'meds' | 'labs' | 'fills'>('overview')

  const { data: patient, isLoading: loadingPatient, isError: patientError } = useQuery({
    queryKey: ['patient', patientId],
    queryFn: () => patientApi.get(patientId).then(r => r.data),
    enabled: !!patientId,
    staleTime: 60_000,
  })

  const { data: allergies = [] } = useQuery({
    queryKey: ['allergies', patientId],
    queryFn: () => patientApi.getAllergies(patientId).then(r => r.data),
    enabled: !!patientId,
    staleTime: 60_000,
  })

  const { data: insurance = [] } = useQuery({
    queryKey: ['insurance', patientId],
    queryFn: () => patientApi.getInsurance(patientId).then(r => r.data),
    enabled: !!patientId,
    staleTime: 120_000,
  })

  const { data: labs = [] } = useQuery<LabResult[]>({
    queryKey: ['labs', patientId],
    queryFn: () => patientApi.getLabs(patientId).then(r => r.data ?? []),
    enabled: !!patientId,
    staleTime: 300_000,
  })

  // Active meds + recent fills both derive from the patient's Rx history —
  // GET /prescriptions?patient_id=… (see rxApi.byPatient). That endpoint
  // already returns drug_name/sig_text/status/fill_date/etc. per row, so we
  // fetch it once and split it client-side rather than standing up two
  // parallel per-patient endpoints.
  const { data: rxHistory = [], isLoading: loadingHistory } = useQuery<PatientRx[]>({
    queryKey: ['rx-history', patientId],
    queryFn: () => rxApi.byPatient(patientId, 25).then(r => r.data ?? []),
    enabled: !!patientId,
    staleTime: 60_000,
  })

  const activeMeds = rxHistory.filter(rx => !DISPENSE_TERMINAL_STATUSES.has(rx.status?.toLowerCase()))
  const recentFills = rxHistory
    .filter(rx => !!rx.fill_date)
    .sort((a, b) => (b.fill_date || '').localeCompare(a.fill_date || ''))
    .slice(0, 5)

  // NOTE: there is no per-patient adherence-score endpoint on the backend
  // (only an aggregate /analytics/adherence/risk-summary exists). Rather than
  // fabricate a score client-side, the adherence risk bar/badge is omitted
  // until a real per-patient endpoint ships.

  if (!patientId) {
    return (
      <div className="p-4 text-center text-gray-400 text-sm space-y-2 pt-8">
        <div className="text-3xl">👤</div>
        <div>Patient profile appears here when an Rx is selected</div>
      </div>
    )
  }

  if (loadingPatient) {
    return (
      <div className="p-3 space-y-3 animate-pulse">
        <div className="h-14 bg-gray-100 rounded" />
        <div className="h-8 bg-gray-100 rounded" />
        <div className="h-24 bg-gray-100 rounded" />
        <div className="h-16 bg-gray-100 rounded" />
      </div>
    )
  }

  // Distinguish "fetch failed / connectivity blip — will retry" from a true
  // "this patient_id genuinely doesn't resolve" — these were previously
  // conflated into one "Patient not found" message, which is misleading
  // during transient network issues (e.g. WS reconnect windows) and reads as
  // a data-integrity problem when it's really just a retry-in-progress state.
  if (patientError) {
    return (
      <div className="p-4 text-sm text-amber-700 bg-amber-50 border border-amber-200 rounded m-3 flex items-start gap-2">
        <span>⚠️</span>
        <span>Couldn't load patient profile — retrying… (check connection)</span>
      </div>
    )
  }

  if (!patient) {
    return <div className="p-4 text-sm text-red-500">Patient not found</div>
  }

  const age = ageFromDob(patient.date_of_birth)
  const dobJalali = patient.date_of_birth_jalali || formatJalali(patient.date_of_birth, { short: true })

  return (
    <div className="flex flex-col h-full text-sm">

      {/* ── Patient header ─────────────────────────────────────────────── */}
      <div className="px-3 py-3 border-b bg-white">
        <div className="flex items-start justify-between gap-2">
          <div>
            <div className="font-bold text-base text-gray-900">
              {patient.last_name?.toUpperCase()}, {patient.first_name}
            </div>
            <div className="text-xs text-gray-500 mt-0.5">
              {/* Jalali primary, Gregorian secondary */}
              ت.ت: {dobJalali}{patient.date_of_birth && ` (${patient.date_of_birth})`} · {age !== null ? `${age}y` : ''} · {patient.gender}
            </div>
            {/* کد ملی — shown for Iranian identity; masked to last 4 digits for display */}
            {patient.national_id && (
              <div className="text-[10px] text-blue-600 font-mono mt-0.5">
                کد ملی: ●●●●●●{patient.national_id.slice(-4)}
              </div>
            )}
            {patient.phone_primary && (
              <div className="text-xs text-gray-400">{patient.phone_primary}</div>
            )}
          </div>
          <div className="flex flex-col items-end gap-1 flex-shrink-0">
            {patient.biometric_enrolled && (
              <span className="text-[10px] bg-green-100 text-green-700 border border-green-300 px-1.5 py-0.5 rounded">
                ✓ Biometric
              </span>
            )}
            {/* Dictate patient note */}
            <DictateNote
              context="note"
              compact
              onConfirm={(text) => console.info('Patient note confirmed:', text.slice(0, 40))}
            />
          </div>
        </div>

        {/* Allergies — always visible, always prominent */}
        {allergies.length > 0 ? (
          <div className="mt-2 flex flex-wrap gap-1">
            {allergies.map((a: { allergen_name: string; severity?: string; reaction?: string }, i: number) => (
              <span key={i}
                className="text-[10px] bg-red-100 text-red-800 border border-red-300 rounded px-1.5 py-0.5 font-semibold"
                title={a.reaction ? `Reaction: ${a.reaction}` : undefined}
              >
                ⚠ {a.allergen_name}
              </span>
            ))}
          </div>
        ) : (
          <div className="mt-1 text-[10px] text-green-600">✓ NKDA</div>
        )}
      </div>

      {/* ── Tab nav ────────────────────────────────────────────────────── */}
      <div className="flex border-b text-[10px] font-medium bg-gray-50">
        {([
          { id: 'overview', label: 'Overview' },
          { id: 'meds',     label: `Meds (${activeMeds.length})` },
          { id: 'labs',     label: `Labs (${labs.length})` },
          { id: 'fills',    label: `Fills (${recentFills.length})` },
        ] as const).map(tab => (
          <button
            key={tab.id}
            onClick={() => setActiveTab(tab.id)}
            className={`flex-1 py-1.5 transition-colors ${
              activeTab === tab.id
                ? 'text-blue-700 border-b-2 border-blue-600 bg-white'
                : 'text-gray-500 hover:text-gray-700'
            }`}
          >
            {tab.label}
          </button>
        ))}
      </div>

      {/* ── Tab content ────────────────────────────────────────────────── */}
      <div className="flex-1 overflow-y-auto p-3 space-y-3">

        {/* OVERVIEW TAB */}
        {activeTab === 'overview' && (
          <>
            {/* Insurance cards */}
            <div>
              <div className="text-xs font-semibold text-gray-600 mb-1.5">Insurance</div>
              {insurance.length === 0 ? (
                <div className="text-xs text-gray-400 italic">Cash pay — no insurance on file</div>
              ) : (
                <div className="space-y-1.5">
                  {[...insurance].sort((a: { priority: number }, b: { priority: number }) => a.priority - b.priority)
                    .map((ins: { priority: number; plan_name?: string; bin_number: string; pcn?: string; member_id: string; group_number?: string }, i: number) => (
                      <div key={i} className="bg-gradient-to-r from-blue-50 to-indigo-50 border border-blue-200 rounded-lg p-2 text-[10px]">
                        <div className="flex items-center justify-between">
                          <span className="font-semibold text-blue-800">{ins.plan_name || `Insurance #${ins.priority}`}</span>
                          <span className="bg-blue-100 text-blue-700 px-1 rounded">{ins.priority}° Priority</span>
                        </div>
                        <div className="text-gray-600 mt-0.5 flex gap-2 flex-wrap">
                          <span>BIN: <strong>{ins.bin_number}</strong></span>
                          {ins.pcn && <span>PCN: <strong>{ins.pcn}</strong></span>}
                          <span>ID: <strong>{ins.member_id}</strong></span>
                          {ins.group_number && <span>Grp: <strong>{ins.group_number}</strong></span>}
                        </div>
                      </div>
                    ))}
                </div>
              )}
            </div>

            {/* Critical lab flags (eGFR) */}
            {labs.some((l: LabResult) => l.test_name?.toLowerCase().includes('egfr') || l.test_name?.toLowerCase().includes('creatinine')) && (
              <div>
                <div className="text-xs font-semibold text-gray-600 mb-1">Key Labs (Renal)</div>
                {labs
                  .filter((l: LabResult) => l.test_name?.toLowerCase().includes('egfr') || l.test_name?.toLowerCase().includes('creatinine'))
                  .slice(0, 3)
                  .map((lab: LabResult, i: number) => {
                    const flag = lab.test_name?.toLowerCase().includes('egfr') ? getEGFRFlag(lab.value) : null
                    return (
                      <div key={i} className={`flex items-center justify-between text-[10px] px-2 py-1.5 rounded border mb-1 ${flag ? 'bg-orange-50 border-orange-300' : 'bg-gray-50 border-gray-200'}`}>
                        <span className="text-gray-600">{lab.test_name}</span>
                        <div className="text-right">
                          <span className={`font-bold ${flag ? 'text-orange-700' : 'text-gray-900'}`}>
                            {lab.value} {lab.unit}
                          </span>
                          {flag && <div className="text-orange-600 text-[9px]">{flag}</div>}
                        </div>
                      </div>
                    )
                  })}
              </div>
            )}
          </>
        )}

        {/* MEDS TAB */}
        {activeTab === 'meds' && (
          <div>
            {loadingHistory ? (
              <div className="space-y-1.5">
                {[...Array(3)].map((_, i) => <div key={i} className="h-12 bg-gray-100 rounded animate-pulse" />)}
              </div>
            ) : activeMeds.length === 0 ? (
              <div className="text-xs text-gray-400 italic text-center py-4">
                No active medications on record
              </div>
            ) : (
              <div className="space-y-1.5">
                {activeMeds.map((med) => (
                  <div key={med.id} className="border rounded-lg p-2 text-[10px]">
                    <div className="flex items-start justify-between gap-1">
                      <div>
                        <span className="font-semibold font-mono text-gray-900">{med.drug_name}</span>
                        {med.drug_strength && <span className="text-gray-500 ml-1">{med.drug_strength}</span>}
                        {med.is_controlled && (
                          <span className="ml-1 bg-orange-100 text-orange-700 border border-orange-200 px-1 rounded text-[9px]">
                            {med.dea_schedule}
                          </span>
                        )}
                      </div>
                      <span className="text-gray-400 uppercase">{med.status}</span>
                    </div>
                    <div className="text-gray-500 mt-0.5 italic">{med.sig_text}</div>
                    <div className="text-gray-400 mt-0.5 flex justify-between">
                      {med.prescriber_name && <span>{med.prescriber_name}</span>}
                      {med.fill_date && <span>Last fill: {dateDisplay(med.fill_date)}</span>}
                    </div>
                  </div>
                ))}
              </div>
            )}
          </div>
        )}

        {/* LABS TAB */}
        {activeTab === 'labs' && (
          <div>
            {labs.length === 0 ? (
              <div className="text-xs text-gray-400 italic text-center py-4">No lab results on file</div>
            ) : (
              <div className="space-y-1">
                {labs.map((lab: LabResult, i: number) => {
                  const eGFRFlag = lab.test_name?.toLowerCase().includes('egfr') ? getEGFRFlag(lab.value) : null
                  return (
                    <div key={i} className={`flex items-center justify-between text-[10px] px-2 py-1.5 rounded border ${lab.is_critical ? 'bg-red-50 border-red-300' : eGFRFlag ? 'bg-orange-50 border-orange-200' : 'bg-gray-50 border-gray-200'}`}>
                      <div>
                        <div className="font-medium text-gray-800">{lab.test_name}</div>
                        <div className="text-gray-400">{lab.collected_at?.slice(0, 10)}</div>
                      </div>
                      <div className="text-right">
                        <div className={`font-bold ${lab.is_critical ? 'text-red-700' : eGFRFlag ? 'text-orange-700' : 'text-gray-900'}`}>
                          {lab.value} {lab.unit}
                        </div>
                        {lab.reference_range && <div className="text-gray-400">{lab.reference_range}</div>}
                        {eGFRFlag && <div className="text-orange-600 text-[9px] max-w-[100px] text-right">{eGFRFlag}</div>}
                      </div>
                    </div>
                  )
                })}
              </div>
            )}
          </div>
        )}

        {/* FILLS TAB */}
        {activeTab === 'fills' && (
          <div>
            {loadingHistory ? (
              <div className="space-y-1.5">
                {[...Array(3)].map((_, i) => <div key={i} className="h-12 bg-gray-100 rounded animate-pulse" />)}
              </div>
            ) : recentFills.length === 0 ? (
              <div className="text-xs text-gray-400 italic text-center py-4">No fill history</div>
            ) : (
              <div className="space-y-1.5">
                {recentFills.map((fill) => (
                  <div key={fill.id} className="border rounded-lg p-2 text-[10px] bg-gray-50">
                    <div className="flex justify-between">
                      <span className="font-semibold font-mono text-gray-800">{fill.drug_name}</span>
                      <span className="text-gray-400 uppercase">{fill.status}</span>
                    </div>
                    <div className="flex justify-between text-gray-400 mt-0.5">
                      <span>Rx #{fill.rx_number}</span>
                      {fill.fill_date && <span>{formatJalali(fill.fill_date, { short: true, persianDigits: false })}</span>}
                    </div>
                    <div className="text-gray-400">{fill.days_supply}d supply</div>
                  </div>
                ))}
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  )
}
