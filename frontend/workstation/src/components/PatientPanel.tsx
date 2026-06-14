/**
 * PatientPanel — Full patient profile in the right sidebar.
 * =========================================================
 * Rich profile: demographics, allergies, insurance, active meds, recent fills,
 * labs (eGFR renal flag), + a dedicated prescriber card and a family/relations
 * footer. "Clinical Daylight" UI upgrade — visual only; every query and derived
 * value is unchanged. Prescriber contact + family tree show honest "pending"
 * states where no backend feed exists yet (per agreed stub policy).
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
  prescriber_medical_council_id?: string | null
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

  // Most recent prescriber on record (real name + medical council ID from Rx
  // history; phone/contact still require a fuller provider-registry lookup).
  const prescriberRx = rxHistory.find(rx => rx.prescriber_name) ?? null
  const prescriberName = prescriberRx?.prescriber_name ?? null
  const councilId = prescriberRx?.prescriber_medical_council_id ?? null

  if (!patientId) {
    return (
      <div className="cd-scope p-4 text-center text-ink3 text-sm space-y-2 pt-8 h-full">
        <div className="text-3xl">👤</div>
        <div>Patient profile appears here when an Rx is selected</div>
      </div>
    )
  }

  if (loadingPatient) {
    return (
      <div className="cd-scope p-3 space-y-3 animate-pulse h-full">
        <div className="h-14 bg-surface2 rounded-lg" />
        <div className="h-8 bg-surface2 rounded-lg" />
        <div className="h-24 bg-surface2 rounded-lg" />
        <div className="h-16 bg-surface2 rounded-lg" />
      </div>
    )
  }

  if (patientError) {
    return (
      <div className="cd-scope h-full p-3">
        <div className="p-4 text-sm text-warning bg-warning-soft border border-warning/30 rounded-lg flex items-start gap-2">
          <span>⚠️</span>
          <span>Couldn't load patient profile — retrying… (check connection)</span>
        </div>
      </div>
    )
  }

  if (!patient) {
    return <div className="cd-scope p-4 text-sm text-blocker h-full">Patient not found</div>
  }

  const age = ageFromDob(patient.date_of_birth)
  const dobJalali = patient.date_of_birth_jalali || formatJalali(patient.date_of_birth, { short: true })

  return (
    <div className="cd-scope flex flex-col h-full text-sm bg-canvas">

      {/* ── Patient header ─────────────────────────────────────────────── */}
      <div className="px-3 py-3 border-b border-line bg-surface">
        <div className="flex items-start justify-between gap-2">
          <div className="min-w-0">
            <div className="cd-ui font-semibold text-base text-ink">
              {patient.last_name?.toUpperCase()}, {patient.first_name}
            </div>
            <div className="text-xs text-ink3 mt-0.5">
              ت.ت: {dobJalali}{patient.date_of_birth && ` (${patient.date_of_birth})`} · {age !== null ? `${age}y` : ''} · {patient.gender}
            </div>
            {patient.national_id && (
              <div className="cd-data text-[10px] text-intel mt-0.5">
                کد ملی: ●●●●●●{patient.national_id.slice(-4)}
              </div>
            )}
            {patient.phone_primary && (
              <div className="cd-data text-xs text-ink3">{patient.phone_primary}</div>
            )}
          </div>
          <div className="flex flex-col items-end gap-1 flex-shrink-0">
            {patient.biometric_enrolled && (
              <span className="cd-data text-[10px] bg-safe-soft text-safe border border-safe/30 px-1.5 py-0.5 rounded">
                ✓ Biometric
              </span>
            )}
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
                className="cd-data text-[10px] bg-blocker-soft text-blocker border border-blocker/30 rounded px-1.5 py-0.5 font-semibold"
                title={a.reaction ? `Reaction: ${a.reaction}` : undefined}
              >
                ⚠ {a.allergen_name}
              </span>
            ))}
          </div>
        ) : (
          <div className="mt-1 text-[10px] text-safe">✓ NKDA</div>
        )}
      </div>

      {/* ── Tab nav ────────────────────────────────────────────────────── */}
      <div className="flex border-b border-line text-[11px] font-medium bg-surface">
        {([
          { id: 'overview', label: 'Overview' },
          { id: 'meds',     label: `Meds (${activeMeds.length})` },
          { id: 'labs',     label: `Labs (${labs.length})` },
          { id: 'fills',    label: `Fills (${recentFills.length})` },
        ] as const).map(tab => (
          <button
            key={tab.id}
            onClick={() => setActiveTab(tab.id)}
            data-active={activeTab === tab.id}
            className={`cd-tab cd-ui flex-1 py-2 ${activeTab === tab.id ? 'text-intel' : 'text-ink3 hover:text-ink2'}`}
          >
            {tab.label}
          </button>
        ))}
      </div>

      {/* ── Tab content ────────────────────────────────────────────────── */}
      <div className="flex-1 overflow-y-auto p-3 space-y-3">

        {activeTab === 'overview' && (
          <div className="space-y-3 cd-section">
            {/* Insurance */}
            <div>
              <div className="cd-ui text-[11px] font-semibold text-ink2 mb-1.5">Insurance</div>
              {insurance.length === 0 ? (
                <div className="text-xs text-ink3 italic">Cash pay — no insurance on file</div>
              ) : (
                <div className="space-y-1.5">
                  {[...insurance].sort((a: { priority: number }, b: { priority: number }) => a.priority - b.priority)
                    .map((ins: { priority: number; plan_name?: string; bin_number: string; pcn?: string; member_id: string; group_number?: string }, i: number) => (
                      <div key={i} className="cd-inset p-2 text-[10px]">
                        <div className="flex items-center justify-between">
                          <span className="cd-ui font-semibold text-intel">{ins.plan_name || `Insurance #${ins.priority}`}</span>
                          <span className="bg-intel-soft text-intel px-1 rounded">{ins.priority}° priority</span>
                        </div>
                        <div className="cd-data text-ink2 mt-0.5 flex gap-2 flex-wrap">
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

            {/* Dedicated prescriber card */}
            <div>
              <div className="cd-ui text-[11px] font-semibold text-ink2 mb-1.5 flex items-center gap-1">Prescriber</div>
              <div className="cd-card p-2.5">
                {prescriberName ? (
                  <>
                    <div className="cd-ui text-sm font-semibold text-ink flex items-center gap-1.5">
                      <span className="w-5 h-5 rounded-md bg-counsel-soft text-counsel flex items-center justify-center text-[11px]">⚕</span>
                      {prescriberName}
                    </div>
                    <div className="grid grid-cols-2 gap-2 mt-2 text-[10px]">
                      <div>
                        <div className="text-ink3">Medical council ID</div>
                        {councilId ? (
                          <a
                            href={`https://www.google.com/search?q=${encodeURIComponent(`نظام پزشکی ${councilId} پزشک`)}`}
                            target="_blank" rel="noopener noreferrer"
                            title="Look up physician on the web"
                            className="cd-data font-bold text-intel hover:underline inline-flex items-center gap-0.5"
                          >
                            {councilId} <span className="text-[8px]">↗</span>
                          </a>
                        ) : (
                          <div className="cd-data font-bold text-ink3 italic">pending</div>
                        )}
                      </div>
                      <div>
                        <div className="text-ink3">Phone</div>
                        <div className="cd-data text-ink3 italic">pending</div>
                      </div>
                    </div>
                    <div className="mt-2 flex gap-3 text-[11px]">
                      <span className="text-intel">📞 Call</span>
                      <span className="text-intel">📠 Fax PA</span>
                      <span className="text-intel">🗂 Record</span>
                    </div>
                    <div className="text-[9px] text-ink3 mt-1.5 italic">Contact details &amp; prescribing history wire from the provider registry.</div>
                  </>
                ) : (
                  <div className="text-xs text-ink3 italic">No prescriber on recent Rx history.</div>
                )}
              </div>
            </div>

            {/* Critical lab flags (eGFR) */}
            {labs.some((l: LabResult) => l.test_name?.toLowerCase().includes('egfr') || l.test_name?.toLowerCase().includes('creatinine')) && (
              <div>
                <div className="cd-ui text-[11px] font-semibold text-ink2 mb-1">Key labs (renal)</div>
                {labs
                  .filter((l: LabResult) => l.test_name?.toLowerCase().includes('egfr') || l.test_name?.toLowerCase().includes('creatinine'))
                  .slice(0, 3)
                  .map((lab: LabResult, i: number) => {
                    const flag = lab.test_name?.toLowerCase().includes('egfr') ? getEGFRFlag(lab.value) : null
                    return (
                      <div key={i} className={`flex items-center justify-between text-[10px] px-2 py-1.5 rounded-lg border mb-1 ${flag ? 'bg-caution-soft border-caution/30' : 'cd-inset'}`}>
                        <span className="text-ink2">{lab.test_name}</span>
                        <div className="text-right">
                          <span className={`cd-data font-bold ${flag ? 'text-caution' : 'text-ink'}`}>
                            {lab.value} {lab.unit}
                          </span>
                          {flag && <div className="text-caution text-[9px]">{flag}</div>}
                        </div>
                      </div>
                    )
                  })}
              </div>
            )}
          </div>
        )}

        {activeTab === 'meds' && (
          <div className="cd-section">
            {loadingHistory ? (
              <div className="space-y-1.5">
                {[...Array(3)].map((_, i) => <div key={i} className="h-12 bg-surface2 rounded-lg animate-pulse" />)}
              </div>
            ) : activeMeds.length === 0 ? (
              <div className="text-xs text-ink3 italic text-center py-4">No active medications on record</div>
            ) : (
              <div className="space-y-1.5">
                {activeMeds.map((med) => (
                  <div key={med.id} className="cd-card p-2 text-[10px]">
                    <div className="flex items-start justify-between gap-1">
                      <div>
                        <span className="cd-data font-semibold text-ink">{med.drug_name}</span>
                        {med.drug_strength && <span className="text-ink3 ml-1">{med.drug_strength}</span>}
                        {med.is_controlled && (
                          <span className="cd-data ml-1 bg-caution-soft text-caution border border-caution/20 px-1 rounded text-[9px]">{med.dea_schedule}</span>
                        )}
                      </div>
                      <span className="text-ink3 uppercase">{med.status}</span>
                    </div>
                    <div className="text-ink2 mt-0.5 italic">{med.sig_text}</div>
                    <div className="text-ink3 mt-0.5 flex justify-between">
                      {med.prescriber_name && <span>{med.prescriber_name}</span>}
                      {med.fill_date && <span>Last fill: {dateDisplay(med.fill_date)}</span>}
                    </div>
                  </div>
                ))}
              </div>
            )}
          </div>
        )}

        {activeTab === 'labs' && (
          <div className="cd-section">
            {labs.length === 0 ? (
              <div className="text-xs text-ink3 italic text-center py-4">No lab results on file</div>
            ) : (
              <div className="space-y-1">
                {labs.map((lab: LabResult, i: number) => {
                  const eGFRFlag = lab.test_name?.toLowerCase().includes('egfr') ? getEGFRFlag(lab.value) : null
                  return (
                    <div key={i} className={`flex items-center justify-between text-[10px] px-2 py-1.5 rounded-lg border ${lab.is_critical ? 'bg-blocker-soft border-blocker/30' : eGFRFlag ? 'bg-caution-soft border-caution/20' : 'cd-inset'}`}>
                      <div>
                        <div className="font-medium text-ink">{lab.test_name}</div>
                        <div className="cd-data text-ink3">{lab.collected_at?.slice(0, 10)}</div>
                      </div>
                      <div className="text-right">
                        <div className={`cd-data font-bold ${lab.is_critical ? 'text-blocker' : eGFRFlag ? 'text-caution' : 'text-ink'}`}>
                          {lab.value} {lab.unit}
                        </div>
                        {lab.reference_range && <div className="cd-data text-ink3">{lab.reference_range}</div>}
                        {eGFRFlag && <div className="text-caution text-[9px] max-w-[100px] text-right">{eGFRFlag}</div>}
                      </div>
                    </div>
                  )
                })}
              </div>
            )}
          </div>
        )}

        {activeTab === 'fills' && (
          <div className="cd-section">
            {loadingHistory ? (
              <div className="space-y-1.5">
                {[...Array(3)].map((_, i) => <div key={i} className="h-12 bg-surface2 rounded-lg animate-pulse" />)}
              </div>
            ) : recentFills.length === 0 ? (
              <div className="text-xs text-ink3 italic text-center py-4">No fill history</div>
            ) : (
              <div className="space-y-1.5">
                {recentFills.map((fill) => (
                  <div key={fill.id} className="cd-inset p-2 text-[10px]">
                    <div className="flex justify-between">
                      <span className="cd-data font-semibold text-ink">{fill.drug_name}</span>
                      <span className="text-ink3 uppercase">{fill.status}</span>
                    </div>
                    <div className="flex justify-between text-ink3 mt-0.5">
                      <span className="cd-data">Rx #{fill.rx_number?.slice(-4)}</span>
                      {fill.fill_date && <span>{formatJalali(fill.fill_date, { short: true, persianDigits: false })}</span>}
                    </div>
                    <div className="text-ink3">{fill.days_supply}d supply</div>
                  </div>
                ))}
              </div>
            )}
          </div>
        )}
      </div>

      {/* ── Family / relations footer (read-only, medical use) ─────────────── */}
      <div className="border-t border-line bg-surface px-3 py-2.5">
        <div className="flex items-center gap-1.5 mb-1.5">
          <span className="cd-ui text-[11px] font-semibold text-ink2">Family / relations</span>
          <span className="text-[9px] text-ink3 ml-auto flex items-center gap-1">🔒 read-only · medical</span>
        </div>
        <div className="cd-inset p-2.5 text-[10px] text-ink3 italic flex items-start gap-2">
          <span>👁</span>
          <span>
            Counter-client identity (face-match + relation tree) and the family graph
            wire from the individuals directory — explored &amp; managed in Admin.
            <span className="not-italic text-ink3"> Data pending.</span>
          </span>
        </div>
      </div>
    </div>
  )
}
