import { useMemo, useState } from 'react'
import { useMutation, useQuery } from '@tanstack/react-query'
import {
  AlertTriangle, ChevronDown, ChevronRight, FlaskConical, Loader2,
  Search, ShieldAlert, Stethoscope,
} from 'lucide-react'
import { clinicalApi, patientApi, rxApi } from '../lib/api'

interface Patient {
  id: string
  first_name: string
  last_name: string
  date_of_birth?: string
  gender?: string
  weight_kg?: number | null
  pregnancy_status?: string | null
  renal_function?: string | null
  hepatic_status?: string | null
  conditions?: string[]
}

interface Allergy {
  id: string
  allergen_name: string
  reaction?: string | null
  severity?: string
}

interface Lab {
  id: string
  test_name: string
  value: string
  unit?: string | null
  result_date: string
}

interface CDSAlert {
  rule_id: string
  severity: 'CRITICAL' | 'HIGH' | 'MODERATE' | 'LOW' | 'INFO'
  title: string
  clinical_problem: string
  mechanism: string
  patient_specific_factors: string[]
  missing_information: string[]
  suggested_pharmacist_actions: string[]
  evidence_sources: string[]
  confidence: number
  pharmacist_verification_notice: string
}

interface CDSResponse {
  patient_id: string
  evaluated_at: string
  model_version: string
  alerts: CDSAlert[]
  missing_information_summary: string[]
  pharmacist_verification_notice: string
  note?: string
}

const severityClass: Record<CDSAlert['severity'], string> = {
  CRITICAL: 'bg-red-500/15 text-red-300 ring-red-500/30',
  HIGH: 'bg-orange-500/15 text-orange-300 ring-orange-500/30',
  MODERATE: 'bg-amber-500/15 text-amber-300 ring-amber-500/30',
  LOW: 'bg-slate-500/15 text-slate-300 ring-slate-500/30',
  INFO: 'bg-gray-500/15 text-gray-300 ring-gray-500/30',
}

function calcAge(dob?: string): number | null {
  if (!dob) return null
  return Math.floor((Date.now() - new Date(dob).getTime()) / (365.25 * 24 * 3600 * 1000))
}

function latestLab(labs: Lab[], matcher: RegExp): Lab | undefined {
  return [...labs]
    .filter(lab => matcher.test(lab.test_name.toLowerCase()))
    .sort((a, b) => new Date(b.result_date).getTime() - new Date(a.result_date).getTime())[0]
}

function DetailList({ title, items, tone = 'slate' }: { title: string; items: string[]; tone?: 'slate' | 'amber' }) {
  if (!items.length) return null
  return (
    <div className={tone === 'amber' ? 'rounded-lg border border-amber-500/25 bg-amber-500/10 p-3' : ''}>
      <p className={`text-[11px] font-semibold uppercase tracking-widest ${tone === 'amber' ? 'text-amber-300' : 'text-slate-500'}`}>{title}</p>
      <ul className="mt-2 space-y-1.5">
        {items.map(item => (
          <li key={item} className="text-xs leading-relaxed text-slate-300">{item}</li>
        ))}
      </ul>
    </div>
  )
}

function AlertCard({ alert }: { alert: CDSAlert }) {
  const [open, setOpen] = useState(alert.severity === 'CRITICAL' || alert.severity === 'HIGH')
  return (
    <div className="rounded-lg border border-[#1e293b] bg-[#171c29]">
      <button
        onClick={() => setOpen(v => !v)}
        className="w-full flex items-start gap-3 px-4 py-3 text-left hover:bg-white/[0.03]"
      >
        <div className="pt-0.5 text-slate-500">
          {open ? <ChevronDown size={16} /> : <ChevronRight size={16} />}
        </div>
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <span className={`rounded-full px-2 py-0.5 text-[11px] font-bold ring-1 ${severityClass[alert.severity]}`}>
              {alert.severity}
            </span>
            <span className="text-sm font-semibold text-slate-100">{alert.title}</span>
          </div>
          <p className="mt-1 text-xs text-slate-400">{alert.clinical_problem}</p>
        </div>
        <span className="rounded bg-[#0f1117] px-2 py-1 text-[11px] font-mono text-slate-300">
          {Math.round(alert.confidence * 100)}%
        </span>
      </button>
      {open && (
        <div className="space-y-4 border-t border-[#1e293b] px-4 py-4">
          <div>
            <p className="text-[11px] font-semibold uppercase tracking-widest text-slate-500">Mechanism</p>
            <p className="mt-1 text-xs leading-relaxed text-slate-300">{alert.mechanism}</p>
          </div>
          <DetailList title="Patient Factors" items={alert.patient_specific_factors} />
          <DetailList title="Missing Information" items={alert.missing_information} tone="amber" />
          <DetailList title="Pharmacist Actions" items={alert.suggested_pharmacist_actions} />
          <div className="rounded-lg border border-[#253047] bg-[#0f1117] p-3">
            <p className="text-[11px] font-semibold uppercase tracking-widest text-slate-500">Sources</p>
            <div className="mt-2 flex flex-wrap gap-2">
              {alert.evidence_sources.map(source => (
                <span key={source} className="rounded bg-slate-800 px-2 py-1 text-[11px] text-slate-300">{source}</span>
              ))}
            </div>
          </div>
          <div className="flex gap-2 rounded-lg border border-blue-500/20 bg-blue-500/10 p-3 text-xs leading-relaxed text-blue-200">
            <ShieldAlert size={15} className="mt-0.5 flex-shrink-0" />
            <span>{alert.pharmacist_verification_notice}</span>
          </div>
        </div>
      )}
    </div>
  )
}

export default function ClinicalAssistant() {
  const [query, setQuery] = useState('')
  const [submitted, setSubmitted] = useState('')
  const [selected, setSelected] = useState<Patient | null>(null)

  const patientSearch = useQuery({
    queryKey: ['cds-patient-search', submitted],
    queryFn: () => patientApi.search(submitted).then(r => r.data as Patient[]),
    enabled: submitted.length >= 2,
  })

  const allergies = useQuery({
    queryKey: ['cds-allergies', selected?.id],
    queryFn: () => patientApi.getAllergies(selected!.id).then(r => r.data as Allergy[]),
    enabled: !!selected,
  })

  const labs = useQuery({
    queryKey: ['cds-labs', selected?.id],
    queryFn: () => patientApi.getLabs(selected!.id).then(r => r.data as Lab[]),
    enabled: !!selected,
  })

  const rxHistory = useQuery({
    queryKey: ['cds-rx-history', selected?.id],
    queryFn: () => rxApi.byPatient(selected!.id, 12).then(r => r.data as Array<{ id: string; drug_name: string; status: string }>),
    enabled: !!selected,
  })

  const evaluate = useMutation({
    mutationFn: (patientId: string) => clinicalApi.evaluateCDS(patientId).then(r => r.data as CDSResponse),
  })

  const keyLabs = useMemo(() => {
    const allLabs = labs.data ?? []
    return [
      latestLab(allLabs, /egfr|glomerular/),
      latestLab(allLabs, /potassium|\bk\b/),
    ].filter(Boolean) as Lab[]
  }, [labs.data])

  const age = calcAge(selected?.date_of_birth)

  return (
    <div className="h-full overflow-y-auto bg-[#0f1117] p-5">
      <div className="mx-auto max-w-7xl space-y-5">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div>
            <h1 className="text-xl font-semibold text-slate-100">Clinical Assistant</h1>
            <p className="mt-1 text-xs text-slate-500">Deterministic medication safety review</p>
          </div>
          {evaluate.data && (
            <div className="rounded-lg border border-[#1e293b] bg-[#171c29] px-3 py-2 text-right">
              <p className="text-[10px] uppercase tracking-widest text-slate-500">Model</p>
              <p className="text-xs font-mono text-slate-300">{evaluate.data.model_version}</p>
            </div>
          )}
        </div>

        <div className="rounded-lg border border-[#1e293b] bg-[#1a1f2e] p-4">
          <div className="flex gap-2">
            <div className="relative flex-1">
              <Search size={15} className="absolute left-3 top-1/2 -translate-y-1/2 text-slate-600" />
              <input
                value={query}
                onChange={event => setQuery(event.target.value)}
                onKeyDown={event => {
                  if (event.key === 'Enter' && query.trim().length >= 2) setSubmitted(query.trim())
                }}
                placeholder="Patient name, DOB, or phone"
                className="w-full rounded-lg border border-[#334155] bg-[#0f1117] py-2.5 pl-9 pr-3 text-sm text-slate-100 placeholder:text-slate-600 focus:border-blue-500 focus:outline-none"
              />
            </div>
            <button
              onClick={() => setSubmitted(query.trim())}
              disabled={query.trim().length < 2 || patientSearch.isFetching}
              className="inline-flex items-center gap-2 rounded-lg bg-blue-600 px-4 py-2.5 text-sm font-medium text-white hover:bg-blue-500 disabled:opacity-40"
            >
              {patientSearch.isFetching ? <Loader2 size={15} className="animate-spin" /> : <Search size={15} />}
              Search
            </button>
          </div>
          {(patientSearch.data?.length ?? 0) > 0 && (
            <div className="mt-3 overflow-hidden rounded-lg border border-[#253047]">
              {patientSearch.data!.map(patient => (
                <button
                  key={patient.id}
                  onClick={() => {
                    setSelected(patient)
                    evaluate.reset()
                    setQuery(`${patient.last_name}, ${patient.first_name}`)
                  }}
                  className="flex w-full items-center justify-between border-b border-[#253047] px-3 py-2.5 text-left last:border-b-0 hover:bg-white/[0.03]"
                >
                  <span className="text-sm font-medium text-slate-200">{patient.last_name}, {patient.first_name}</span>
                  <span className="text-xs text-slate-500">{patient.date_of_birth} · {patient.gender}</span>
                </button>
              ))}
            </div>
          )}
        </div>

        {selected ? (
          <div className="grid grid-cols-1 gap-5 xl:grid-cols-[360px_1fr]">
            <aside className="space-y-5">
              <div className="rounded-lg border border-[#1e293b] bg-[#1a1f2e] p-4">
                <div className="flex items-center gap-2">
                  <Stethoscope size={18} className="text-blue-300" />
                  <h2 className="text-sm font-semibold text-slate-100">{selected.first_name} {selected.last_name}</h2>
                </div>
                <dl className="mt-4 grid grid-cols-2 gap-3 text-xs">
                  <div><dt className="text-slate-500">Age</dt><dd className="mt-1 text-slate-200">{age ?? 'Unknown'}</dd></div>
                  <div><dt className="text-slate-500">Sex</dt><dd className="mt-1 text-slate-200">{selected.gender || 'Unknown'}</dd></div>
                  <div><dt className="text-slate-500">Weight</dt><dd className="mt-1 text-slate-200">{selected.weight_kg ? `${selected.weight_kg} kg` : 'Unknown'}</dd></div>
                  <div><dt className="text-slate-500">Pregnancy</dt><dd className="mt-1 text-slate-200">{selected.pregnancy_status || 'Unknown'}</dd></div>
                  <div><dt className="text-slate-500">Renal</dt><dd className="mt-1 text-slate-200">{selected.renal_function || 'Unknown'}</dd></div>
                  <div><dt className="text-slate-500">Hepatic</dt><dd className="mt-1 text-slate-200">{selected.hepatic_status || 'Unknown'}</dd></div>
                </dl>
                <div className="mt-4 space-y-3">
                  <div>
                    <p className="text-[11px] font-semibold uppercase tracking-widest text-slate-500">Conditions</p>
                    <div className="mt-2 flex flex-wrap gap-2">
                      {(selected.conditions?.length ? selected.conditions : ['None recorded']).map(item => (
                        <span key={item} className="rounded bg-slate-800 px-2 py-1 text-[11px] text-slate-300">{item}</span>
                      ))}
                    </div>
                  </div>
                  <div>
                    <p className="text-[11px] font-semibold uppercase tracking-widest text-slate-500">Allergies</p>
                    <div className="mt-2 flex flex-wrap gap-2">
                      {(allergies.data?.length ? allergies.data : []).map(allergy => (
                        <span key={allergy.id} className="rounded bg-red-500/15 px-2 py-1 text-[11px] font-medium text-red-300 ring-1 ring-red-500/30">
                          {allergy.allergen_name}
                        </span>
                      ))}
                      {!allergies.isLoading && !allergies.data?.length && <span className="text-xs text-slate-500">None recorded</span>}
                    </div>
                  </div>
                  <div>
                    <p className="text-[11px] font-semibold uppercase tracking-widest text-slate-500">Key Labs</p>
                    <div className="mt-2 space-y-2">
                      {keyLabs.map(lab => (
                        <div key={lab.id} className="flex items-center justify-between rounded bg-[#0f1117] px-3 py-2 text-xs">
                          <span className="text-slate-300">{lab.test_name}</span>
                          <span className="font-mono text-slate-100">{lab.value} {lab.unit}</span>
                        </div>
                      ))}
                      {!labs.isLoading && keyLabs.length === 0 && <p className="text-xs text-slate-500">No eGFR or potassium on file</p>}
                    </div>
                  </div>
                </div>
              </div>

              <div className="rounded-lg border border-[#1e293b] bg-[#1a1f2e] p-4">
                <div className="flex items-center gap-2">
                  <FlaskConical size={17} className="text-slate-400" />
                  <h2 className="text-sm font-semibold text-slate-100">Medication Sources</h2>
                </div>
                <div className="mt-3 space-y-2">
                  {(rxHistory.data ?? []).slice(0, 8).map(rx => (
                    <div key={rx.id} className="rounded bg-[#0f1117] px-3 py-2">
                      <p className="truncate text-xs font-medium text-slate-300">{rx.drug_name}</p>
                      <p className="mt-0.5 text-[11px] capitalize text-slate-600">{rx.status?.replace(/_/g, ' ')}</p>
                    </div>
                  ))}
                  {!rxHistory.isLoading && !rxHistory.data?.length && <p className="text-xs text-slate-500">No prescription rows found</p>}
                </div>
                <button
                  onClick={() => evaluate.mutate(selected.id)}
                  disabled={evaluate.isPending}
                  className="mt-4 inline-flex w-full items-center justify-center gap-2 rounded-lg bg-blue-600 px-4 py-2.5 text-sm font-semibold text-white hover:bg-blue-500 disabled:opacity-40"
                >
                  {evaluate.isPending ? <Loader2 size={15} className="animate-spin" /> : <ShieldAlert size={15} />}
                  Evaluate CDS
                </button>
              </div>
            </aside>

            <main className="space-y-4">
              <div className="rounded-lg border border-[#1e293b] bg-[#1a1f2e] p-4">
                <div className="flex items-center justify-between gap-3">
                  <div>
                    <h2 className="text-sm font-semibold text-slate-100">Clinical Alerts</h2>
                    <p className="mt-1 text-xs text-slate-500">
                      {evaluate.data ? `${evaluate.data.alerts.length} alert${evaluate.data.alerts.length === 1 ? '' : 's'} · ${new Date(evaluate.data.evaluated_at).toLocaleString()}` : 'No evaluation yet'}
                    </p>
                  </div>
                  {evaluate.data?.missing_information_summary.length ? (
                    <span className="rounded-full bg-amber-500/15 px-3 py-1 text-xs font-semibold text-amber-300 ring-1 ring-amber-500/30">
                      Missing data
                    </span>
                  ) : null}
                </div>
                {evaluate.isError && (
                  <div className="mt-4 flex gap-2 rounded-lg border border-red-500/25 bg-red-500/10 p-3 text-sm text-red-200">
                    <AlertTriangle size={16} className="mt-0.5 flex-shrink-0" />
                    CDS evaluation failed.
                  </div>
                )}
                {evaluate.data?.note && (
                  <p className="mt-4 rounded-lg border border-[#253047] bg-[#0f1117] p-3 text-sm text-slate-400">{evaluate.data.note}</p>
                )}
                {evaluate.data?.missing_information_summary.length ? (
                  <div className="mt-4 rounded-lg border border-amber-500/25 bg-amber-500/10 p-3">
                    <p className="text-[11px] font-semibold uppercase tracking-widest text-amber-300">Missing Information</p>
                    <div className="mt-2 flex flex-wrap gap-2">
                      {evaluate.data.missing_information_summary.map(item => (
                        <span key={item} className="rounded bg-amber-500/15 px-2 py-1 text-xs text-amber-200">{item}</span>
                      ))}
                    </div>
                  </div>
                ) : null}
                <div className="mt-4 space-y-3">
                  {evaluate.data?.alerts.map(alert => <AlertCard key={`${alert.rule_id}-${alert.severity}`} alert={alert} />)}
                  {evaluate.data && evaluate.data.alerts.length === 0 && (
                    <div className="rounded-lg border border-[#253047] bg-[#0f1117] p-5 text-center text-sm text-slate-500">
                      No deterministic CDS alerts returned.
                    </div>
                  )}
                </div>
                {evaluate.data && (
                  <div className="mt-4 flex gap-2 rounded-lg border border-blue-500/20 bg-blue-500/10 p-3 text-xs leading-relaxed text-blue-200">
                    <ShieldAlert size={15} className="mt-0.5 flex-shrink-0" />
                    <span>{evaluate.data.pharmacist_verification_notice}</span>
                  </div>
                )}
              </div>
            </main>
          </div>
        ) : (
          <div className="rounded-lg border border-dashed border-[#334155] bg-[#1a1f2e] p-8 text-center text-sm text-slate-500">
            Select a patient to evaluate deterministic CDS rules.
          </div>
        )}
      </div>
    </div>
  )
}
