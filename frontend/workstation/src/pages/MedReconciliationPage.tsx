import { useMemo, useState } from 'react'
import { useMutation } from '@tanstack/react-query'
import {
  AlertTriangle, CheckCircle2, ClipboardList, Loader2, Plus, ShieldAlert,
  Stethoscope, Trash2,
} from 'lucide-react'
import PatientSearch from '../components/PatientSearch'
import {
  clinicalApi,
  type MedReconcileDiscrepancy,
  type MedReconcileMedicationInput,
  type MedReconcilePayload,
  type MedReconcileResponse,
} from '../lib/api'

interface Patient {
  id: string
  first_name: string
  last_name: string
  date_of_birth: string
  gender: string
  phone_primary?: string
  status: string
  biometric_enrolled: boolean
}

type SourceMode = 'home' | 'admission' | 'discharge' | 'prescriber' | 'patient_reported' | 'manual'

const SOURCE_OPTIONS: Array<{ value: SourceMode; label: string }> = [
  { value: 'home', label: 'home' },
  { value: 'admission', label: 'admission' },
  { value: 'discharge', label: 'discharge' },
  { value: 'prescriber', label: 'prescriber' },
  { value: 'patient_reported', label: 'patient_reported' },
  { value: 'manual', label: 'manual entry' },
]

const typeClass: Record<string, string> = {
  OMITTED: 'bg-red-500/15 text-red-300 ring-red-500/30',
  DOSE_CHANGE: 'bg-orange-500/15 text-orange-300 ring-orange-500/30',
  ADDED: 'bg-blue-500/15 text-blue-300 ring-blue-500/30',
}

const severityClass: Record<MedReconcileDiscrepancy['severity'], string> = {
  high: 'bg-red-600 text-white ring-red-400/40',
  moderate: 'bg-amber-500 text-amber-950 ring-amber-300/40',
  low: 'bg-slate-600 text-white ring-slate-400/40',
}

function calcAge(dob?: string): number | null {
  if (!dob) return null
  return Math.floor((Date.now() - new Date(dob).getTime()) / (365.25 * 24 * 3600 * 1000))
}

function emptyMed(): MedReconcileMedicationInput {
  return { drug_name: '', strength: '', route: '', frequency: '', source: 'manual' }
}

function cleanManualRows(rows: MedReconcileMedicationInput[], source: string): MedReconcileMedicationInput[] {
  return rows
    .filter(row => row.drug_name.trim())
    .map(row => ({
      drug_name: row.drug_name.trim(),
      strength: row.strength?.trim() || undefined,
      dose: row.dose?.trim() || undefined,
      route: row.route?.trim() || undefined,
      frequency: row.frequency?.trim() || undefined,
      source,
      status: 'active',
    }))
}

function EntryBlock({ title, entry }: { title: string; entry: MedReconcileMedicationInput | null }) {
  return (
    <div className="rounded-lg border border-[#253047] bg-[#0f1117] p-3">
      <p className="text-[11px] font-semibold uppercase tracking-widest text-slate-500">{title}</p>
      {entry ? (
        <dl className="mt-3 grid grid-cols-2 gap-2 text-xs">
          <div className="col-span-2"><dt className="text-slate-500">Drug</dt><dd className="mt-1 text-slate-200">{entry.drug_name}</dd></div>
          <div><dt className="text-slate-500">Strength</dt><dd className="mt-1 text-slate-300">{entry.strength || entry.dose || 'Not documented'}</dd></div>
          <div><dt className="text-slate-500">Route</dt><dd className="mt-1 text-slate-300">{entry.route || 'Not documented'}</dd></div>
          <div><dt className="text-slate-500">Frequency</dt><dd className="mt-1 text-slate-300">{entry.frequency || 'Not documented'}</dd></div>
          <div><dt className="text-slate-500">Status</dt><dd className="mt-1 text-slate-300">{entry.status || 'Unknown'}</dd></div>
        </dl>
      ) : (
        <p className="mt-3 text-xs text-slate-500">Not found</p>
      )}
    </div>
  )
}

function DiscrepancyCard({ discrepancy }: { discrepancy: MedReconcileDiscrepancy }) {
  return (
    <div className="rounded-lg border border-[#1e293b] bg-[#171c29] p-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-2">
            <span className={`rounded-full px-2.5 py-1 text-[11px] font-bold ring-1 ${typeClass[discrepancy.discrepancy_type] || 'bg-slate-500/15 text-slate-300 ring-slate-500/30'}`}>
              {discrepancy.discrepancy_type.replace(/_/g, ' ')}
            </span>
            <span className={`rounded-full px-2.5 py-1 text-[11px] font-bold capitalize ring-1 ${severityClass[discrepancy.severity]}`}>
              {discrepancy.severity}
            </span>
            <span className="rounded bg-[#0f1117] px-2 py-1 text-[11px] text-slate-400">
              {Math.round(discrepancy.confidence * 100)}%
            </span>
          </div>
          <h2 className="mt-3 text-base font-semibold text-slate-100">{discrepancy.drug_name}</h2>
        </div>
        <AlertTriangle size={18} className={discrepancy.severity === 'high' ? 'text-red-300' : 'text-slate-500'} />
      </div>

      <div className="mt-4 grid grid-cols-1 gap-3 lg:grid-cols-2">
        <EntryBlock title={discrepancy.source_a_label} entry={discrepancy.source_a_entry} />
        <EntryBlock title={discrepancy.source_b_label} entry={discrepancy.source_b_entry} />
      </div>

      <div className="mt-3 rounded-lg border border-[#253047] bg-[#0f1117] p-3">
        <p className="text-[11px] font-semibold uppercase tracking-widest text-slate-500">Explanation</p>
        <p className="mt-2 text-xs leading-relaxed text-slate-300">{discrepancy.explanation}</p>
      </div>
      <div className="mt-3 rounded-lg border border-blue-500/20 bg-blue-500/10 p-3">
        <p className="text-[11px] font-semibold uppercase tracking-widest text-blue-300">Pharmacist Action</p>
        <p className="mt-2 text-xs font-medium leading-relaxed text-blue-100">{discrepancy.suggested_pharmacist_action}</p>
      </div>
    </div>
  )
}

function ManualRows({
  label,
  rows,
  onRowsChange,
}: {
  label: string
  rows: MedReconcileMedicationInput[]
  onRowsChange: (rows: MedReconcileMedicationInput[]) => void
}) {
  const updateRow = (index: number, patch: Partial<MedReconcileMedicationInput>) => {
    onRowsChange(rows.map((row, rowIndex) => (rowIndex === index ? { ...row, ...patch } : row)))
  }

  return (
    <div className="mt-3 space-y-2">
      <p className="text-[11px] font-semibold uppercase tracking-widest text-slate-500">{label}</p>
      {rows.map((row, index) => (
        <div key={index} className="grid grid-cols-1 gap-2 rounded-lg border border-[#253047] bg-[#0f1117] p-2 sm:grid-cols-[1.4fr_1fr_1fr_1fr_auto]">
          <input
            value={row.drug_name}
            onChange={event => updateRow(index, { drug_name: event.target.value })}
            placeholder="Drug"
            className="rounded border border-[#334155] bg-[#101622] px-2.5 py-2 text-xs text-slate-100 placeholder:text-slate-600 focus:border-blue-500 focus:outline-none"
          />
          <input
            value={row.strength || ''}
            onChange={event => updateRow(index, { strength: event.target.value })}
            placeholder="Strength"
            className="rounded border border-[#334155] bg-[#101622] px-2.5 py-2 text-xs text-slate-100 placeholder:text-slate-600 focus:border-blue-500 focus:outline-none"
          />
          <input
            value={row.route || ''}
            onChange={event => updateRow(index, { route: event.target.value })}
            placeholder="Route"
            className="rounded border border-[#334155] bg-[#101622] px-2.5 py-2 text-xs text-slate-100 placeholder:text-slate-600 focus:border-blue-500 focus:outline-none"
          />
          <input
            value={row.frequency || ''}
            onChange={event => updateRow(index, { frequency: event.target.value })}
            placeholder="Frequency"
            className="rounded border border-[#334155] bg-[#101622] px-2.5 py-2 text-xs text-slate-100 placeholder:text-slate-600 focus:border-blue-500 focus:outline-none"
          />
          <button
            type="button"
            onClick={() => onRowsChange(rows.filter((_, rowIndex) => rowIndex !== index))}
            className="inline-flex h-9 items-center justify-center rounded border border-[#334155] px-2 text-slate-400 hover:bg-white/[0.04] hover:text-slate-200"
            title="Remove row"
          >
            <Trash2 size={14} />
          </button>
        </div>
      ))}
      <button
        type="button"
        onClick={() => onRowsChange([...rows, emptyMed()])}
        className="inline-flex items-center gap-2 rounded-lg border border-[#334155] px-3 py-2 text-xs font-medium text-slate-300 hover:bg-white/[0.04]"
      >
        <Plus size={14} />
        Add medication
      </button>
    </div>
  )
}

function SummaryBadge({ label, count, tone }: { label: string; count: number; tone: string }) {
  return (
    <div className={`rounded-lg border px-3 py-2 ${tone}`}>
      <p className="text-[10px] font-semibold uppercase tracking-widest opacity-80">{label}</p>
      <p className="mt-1 text-2xl font-semibold tabular-nums">{count}</p>
    </div>
  )
}

export default function MedReconciliationPage() {
  const [selected, setSelected] = useState<Patient | null>(null)
  const [sourceALabel, setSourceALabel] = useState('Home Medications')
  const [sourceBLabel, setSourceBLabel] = useState('Admission Medications')
  const [sourceAMode, setSourceAMode] = useState<SourceMode>('home')
  const [sourceBMode, setSourceBMode] = useState<SourceMode>('admission')
  const [sourceAMeds, setSourceAMeds] = useState<MedReconcileMedicationInput[]>([emptyMed()])
  const [sourceBMeds, setSourceBMeds] = useState<MedReconcileMedicationInput[]>([emptyMed()])

  const assessment = useMutation({
    mutationFn: (payload: MedReconcilePayload) =>
      clinicalApi.medReconcile(payload).then(r => r.data as MedReconcileResponse),
  })

  const age = calcAge(selected?.date_of_birth)
  const counts = useMemo(() => {
    const next = { omitted: 0, dose: 0, added: 0, other: 0 }
    for (const item of assessment.data?.discrepancies ?? []) {
      if (item.discrepancy_type === 'OMITTED') next.omitted += 1
      else if (item.discrepancy_type === 'DOSE_CHANGE') next.dose += 1
      else if (item.discrepancy_type === 'ADDED') next.added += 1
      else next.other += 1
    }
    return next
  }, [assessment.data?.discrepancies])

  const grouped = useMemo(() => {
    const order = ['OMITTED', 'DOSE_CHANGE', 'ADDED']
    const data = assessment.data?.discrepancies ?? []
    return [
      ...order.map(type => ({ type, items: data.filter(item => item.discrepancy_type === type) })),
      { type: 'OTHER', items: data.filter(item => !order.includes(item.discrepancy_type)) },
    ].filter(group => group.items.length)
  }, [assessment.data?.discrepancies])

  const submit = () => {
    if (!selected) return
    const payload: MedReconcilePayload = {
      patient_id: selected.id,
      source_a_label: sourceALabel.trim() || 'Source A',
      source_b_label: sourceBLabel.trim() || 'Source B',
    }
    if (sourceAMode === 'manual') payload.source_a_meds = cleanManualRows(sourceAMeds, 'manual_a')
    else payload.source_a = sourceAMode
    if (sourceBMode === 'manual') payload.source_b_meds = cleanManualRows(sourceBMeds, 'manual_b')
    else payload.source_b = sourceBMode
    assessment.mutate(payload)
  }

  return (
    <div className="h-full overflow-y-auto bg-[#0f1117] p-5">
      <div className="mx-auto max-w-7xl space-y-5">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div>
            <h1 className="text-xl font-semibold text-slate-100">Medication Reconciliation</h1>
            <p className="mt-1 text-xs text-slate-500">Deterministic source-to-source medication comparison</p>
          </div>
          {assessment.data && (
            <div className="rounded-lg border border-[#1e293b] bg-[#171c29] px-3 py-2 text-right">
              <p className="text-[10px] uppercase tracking-widest text-slate-500">Reconciled</p>
              <p className="text-xs font-mono text-slate-300">{new Date(assessment.data.assessment_date).toLocaleString()}</p>
            </div>
          )}
        </div>

        <div className="rounded-lg border border-[#1e293b] bg-[#1a1f2e] p-4">
          <PatientSearch
            placeholder="Search patient name, DOB, or phone"
            onSelect={(patient) => {
              setSelected(patient)
              assessment.reset()
            }}
          />
        </div>

        {selected ? (
          <div className="grid grid-cols-1 gap-5 xl:grid-cols-[420px_1fr]">
            <aside className="space-y-5">
              <div className="rounded-lg border border-[#1e293b] bg-[#1a1f2e] p-4">
                <div className="flex items-center gap-2">
                  <Stethoscope size={18} className="text-blue-300" />
                  <h2 className="text-sm font-semibold text-slate-100">{selected.first_name} {selected.last_name}</h2>
                </div>
                <dl className="mt-4 grid grid-cols-2 gap-3 text-xs">
                  <div><dt className="text-slate-500">Age</dt><dd className="mt-1 text-slate-200">{age ?? 'Unknown'}</dd></div>
                  <div><dt className="text-slate-500">Sex</dt><dd className="mt-1 text-slate-200">{selected.gender || 'Unknown'}</dd></div>
                </dl>
              </div>

              <div className="rounded-lg border border-[#1e293b] bg-[#1a1f2e] p-4">
                <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
                  <label className="text-xs text-slate-500">
                    Source A label
                    <input
                      value={sourceALabel}
                      onChange={event => setSourceALabel(event.target.value)}
                      className="mt-1 w-full rounded-lg border border-[#334155] bg-[#0f1117] px-3 py-2 text-sm text-slate-100 focus:border-blue-500 focus:outline-none"
                    />
                  </label>
                  <label className="text-xs text-slate-500">
                    Source B label
                    <input
                      value={sourceBLabel}
                      onChange={event => setSourceBLabel(event.target.value)}
                      className="mt-1 w-full rounded-lg border border-[#334155] bg-[#0f1117] px-3 py-2 text-sm text-slate-100 focus:border-blue-500 focus:outline-none"
                    />
                  </label>
                  <label className="text-xs text-slate-500">
                    Source A
                    <select
                      value={sourceAMode}
                      onChange={event => setSourceAMode(event.target.value as SourceMode)}
                      className="mt-1 w-full rounded-lg border border-[#334155] bg-[#0f1117] px-3 py-2 text-sm text-slate-100 focus:border-blue-500 focus:outline-none"
                    >
                      {SOURCE_OPTIONS.map(option => <option key={option.value} value={option.value}>{option.label}</option>)}
                    </select>
                  </label>
                  <label className="text-xs text-slate-500">
                    Source B
                    <select
                      value={sourceBMode}
                      onChange={event => setSourceBMode(event.target.value as SourceMode)}
                      className="mt-1 w-full rounded-lg border border-[#334155] bg-[#0f1117] px-3 py-2 text-sm text-slate-100 focus:border-blue-500 focus:outline-none"
                    >
                      {SOURCE_OPTIONS.map(option => <option key={option.value} value={option.value}>{option.label}</option>)}
                    </select>
                  </label>
                </div>

                {sourceAMode === 'manual' && (
                  <ManualRows label={sourceALabel || 'Source A'} rows={sourceAMeds} onRowsChange={setSourceAMeds} />
                )}
                {sourceBMode === 'manual' && (
                  <ManualRows label={sourceBLabel || 'Source B'} rows={sourceBMeds} onRowsChange={setSourceBMeds} />
                )}

                <button
                  onClick={submit}
                  disabled={assessment.isPending}
                  className="mt-4 inline-flex w-full items-center justify-center gap-2 rounded-lg bg-blue-600 px-4 py-2.5 text-sm font-semibold text-white hover:bg-blue-500 disabled:opacity-40"
                >
                  {assessment.isPending ? <Loader2 size={15} className="animate-spin" /> : <ClipboardList size={15} />}
                  Reconcile Medications
                </button>
              </div>
            </aside>

            <main className="space-y-4">
              {assessment.data && (
                <div className="grid grid-cols-2 gap-3 lg:grid-cols-5">
                  <SummaryBadge label="Total" count={assessment.data.discrepancies.length} tone="border-slate-500/30 bg-slate-500/10 text-slate-200" />
                  <SummaryBadge label="Omitted" count={counts.omitted} tone="border-red-500/30 bg-red-500/10 text-red-200" />
                  <SummaryBadge label="Dose Changes" count={counts.dose} tone="border-orange-500/30 bg-orange-500/10 text-orange-200" />
                  <SummaryBadge label="Added" count={counts.added} tone="border-blue-500/30 bg-blue-500/10 text-blue-200" />
                  <SummaryBadge label="Reconciled" count={assessment.data.reconciled_count} tone="border-emerald-500/30 bg-emerald-500/10 text-emerald-200" />
                </div>
              )}

              {assessment.data?.discrepancies.length === 0 && (
                <div className="flex gap-2 rounded-lg border border-emerald-500/25 bg-emerald-500/10 p-4 text-sm text-emerald-200">
                  <CheckCircle2 size={18} className="mt-0.5 flex-shrink-0" />
                  <span>All medications reconciled - no discrepancies found</span>
                </div>
              )}

              {grouped.map(group => (
                <section key={group.type} className="space-y-3">
                  <h2 className="text-sm font-semibold text-slate-300">{group.type === 'OTHER' ? 'Other Discrepancies' : group.type.replace(/_/g, ' ')}</h2>
                  {group.items.map(item => <DiscrepancyCard key={item.discrepancy_id} discrepancy={item} />)}
                </section>
              ))}

              {assessment.data && (
                <div className="flex gap-2 rounded-lg border border-[#1e293b] bg-[#171c29] p-3 text-xs italic leading-relaxed text-slate-400">
                  <ShieldAlert size={15} className="mt-0.5 flex-shrink-0 text-slate-500" />
                  <span>{assessment.data.pharmacist_verification_notice}</span>
                </div>
              )}

              {!assessment.data && (
                <div className="rounded-lg border border-dashed border-[#253047] bg-[#1a1f2e] p-10 text-center">
                  <ClipboardList size={28} className="mx-auto text-slate-600" />
                  <p className="mt-3 text-sm text-slate-500">Select a patient and compare medication sources.</p>
                </div>
              )}
            </main>
          </div>
        ) : (
          <div className="rounded-lg border border-dashed border-[#253047] bg-[#1a1f2e] p-10 text-center">
            <Stethoscope size={28} className="mx-auto text-slate-600" />
            <p className="mt-3 text-sm text-slate-500">Search and select a patient.</p>
          </div>
        )}
      </div>
    </div>
  )
}
