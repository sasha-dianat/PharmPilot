import { useState } from 'react'
import { useMutation, useQuery } from '@tanstack/react-query'
import {
  AlertTriangle, CalendarDays, CheckSquare, ClipboardCheck, FlaskConical,
  Loader2, Search, ShieldAlert, Stethoscope,
} from 'lucide-react'
import { clinicalApi, patientApi, rxApi } from '../lib/api'

interface Patient {
  id: string
  first_name: string
  last_name: string
  date_of_birth?: string
  gender?: string
}

interface ADRCause {
  drug: string
  reaction: string
  causality: 'probable' | 'possible' | 'unlikely' | 'unclear'
  seriousness: 'serious' | 'moderate' | 'mild'
  reasoning: string[]
  alternative_explanations: string[]
  questions_to_ask: string[]
  suggested_pharmacist_action: string
  urgency: 'high' | 'routine' | 'low' | 'unknown'
  evidence_sources: string[]
  missing_information: string[]
}

interface ADRResponse {
  patient_id: string
  complaint: string
  assessed_at: string
  model_version: string
  suspected_causes: ADRCause[]
  llm_used: boolean
  degraded: boolean
  pharmacist_verification_notice: string
  not_a_diagnosis_notice: string
  note?: string
}

const causalityClass: Record<ADRCause['causality'], string> = {
  probable: 'bg-red-500/15 text-red-300 ring-red-500/30',
  possible: 'bg-amber-500/15 text-amber-300 ring-amber-500/30',
  unlikely: 'bg-slate-500/15 text-slate-300 ring-slate-500/30',
  unclear: 'bg-gray-500/15 text-gray-300 ring-gray-500/30',
}

function DetailList({ title, items, tone = 'slate', checklist = false }: {
  title: string
  items: string[]
  tone?: 'slate' | 'amber'
  checklist?: boolean
}) {
  if (!items.length) return null
  return (
    <div className={tone === 'amber' ? 'rounded-lg border border-amber-500/25 bg-amber-500/10 p-3' : ''}>
      <p className={`text-[11px] font-semibold uppercase tracking-widest ${tone === 'amber' ? 'text-amber-300' : 'text-slate-500'}`}>{title}</p>
      <ul className="mt-2 space-y-1.5">
        {items.map(item => (
          <li key={item} className="flex gap-2 text-xs leading-relaxed text-slate-300">
            {checklist && <CheckSquare size={13} className="mt-0.5 flex-shrink-0 text-slate-500" />}
            <span>{item}</span>
          </li>
        ))}
      </ul>
    </div>
  )
}

function CauseCard({ cause }: { cause: ADRCause }) {
  return (
    <div className="rounded-lg border border-[#1e293b] bg-[#171c29] p-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-2">
            <span className={`rounded-full px-2 py-0.5 text-[11px] font-bold capitalize ring-1 ${causalityClass[cause.causality]}`}>
              {cause.causality}
            </span>
            <span className="rounded bg-[#0f1117] px-2 py-1 text-[11px] font-semibold uppercase tracking-wide text-slate-400">
              {cause.seriousness}
            </span>
            <span className="rounded bg-[#0f1117] px-2 py-1 text-[11px] font-semibold uppercase tracking-wide text-slate-400">
              {cause.urgency} urgency
            </span>
          </div>
          <h2 className="mt-3 text-base font-semibold text-slate-100">{cause.drug}</h2>
          <p className="mt-1 text-sm text-slate-400">{cause.reaction.replace(/_/g, ' ')}</p>
        </div>
        <ClipboardCheck size={18} className="text-blue-300" />
      </div>

      <div className="mt-4 grid grid-cols-1 gap-4 xl:grid-cols-2">
        <DetailList title="Reasoning" items={cause.reasoning} />
        <DetailList title="Questions To Ask" items={cause.questions_to_ask} checklist />
        <DetailList title="Alternative Explanations" items={cause.alternative_explanations} />
        <DetailList title="Missing Information" items={cause.missing_information} tone="amber" />
      </div>

      <div className="mt-4 rounded-lg border border-[#253047] bg-[#0f1117] p-3">
        <p className="text-[11px] font-semibold uppercase tracking-widest text-slate-500">Pharmacist Action</p>
        <p className="mt-2 text-xs leading-relaxed text-slate-300">{cause.suggested_pharmacist_action}</p>
      </div>

      <div className="mt-3 rounded-lg border border-[#253047] bg-[#0f1117] p-3">
        <p className="text-[11px] font-semibold uppercase tracking-widest text-slate-500">Evidence Sources</p>
        <div className="mt-2 flex flex-wrap gap-2">
          {cause.evidence_sources.map(source => (
            <span key={source} className="rounded bg-slate-800 px-2 py-1 text-[11px] text-slate-300">{source}</span>
          ))}
        </div>
      </div>
    </div>
  )
}

export default function ADRDetective() {
  const [query, setQuery] = useState('')
  const [submitted, setSubmitted] = useState('')
  const [selected, setSelected] = useState<Patient | null>(null)
  const [complaint, setComplaint] = useState('')
  const [onsetDate, setOnsetDate] = useState('')

  const patientSearch = useQuery({
    queryKey: ['adr-patient-search', submitted],
    queryFn: () => patientApi.search(submitted).then(r => r.data as Patient[]),
    enabled: submitted.length >= 2,
  })

  const rxHistory = useQuery({
    queryKey: ['adr-rx-history', selected?.id],
    queryFn: () => rxApi.byPatient(selected!.id, 12).then(r => r.data as Array<{ id: string; drug_name: string; status: string }>),
    enabled: !!selected,
  })

  const assess = useMutation({
    mutationFn: () => clinicalApi
      .assessADR(selected!.id, complaint.trim(), onsetDate || undefined)
      .then(r => r.data as ADRResponse),
  })

  const resultMode = assess.data?.llm_used
    ? 'LLM-assisted prose'
    : assess.data?.degraded
      ? 'Deterministic fallback'
      : 'Deterministic'

  return (
    <div className="h-full overflow-y-auto bg-[#0f1117] p-5">
      <div className="mx-auto max-w-7xl space-y-5">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div>
            <h1 className="text-xl font-semibold text-slate-100">ADR Detective</h1>
            <p className="mt-1 text-xs text-slate-500">Deterministic side-effect assessment with guarded prose</p>
          </div>
          {assess.data && (
            <div className="rounded-lg border border-[#1e293b] bg-[#171c29] px-3 py-2 text-right">
              <p className="text-[10px] uppercase tracking-widest text-slate-500">Mode</p>
              <p className="text-xs font-mono text-slate-300">{resultMode}</p>
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
                    assess.reset()
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
                <div className="mt-4 space-y-3">
                  <label className="block">
                    <span className="text-[11px] font-semibold uppercase tracking-widest text-slate-500">Complaint</span>
                    <textarea
                      value={complaint}
                      onChange={event => setComplaint(event.target.value)}
                      rows={5}
                      placeholder="e.g. dry cough for three weeks"
                      className="mt-2 w-full resize-none rounded-lg border border-[#334155] bg-[#0f1117] px-3 py-2.5 text-sm text-slate-100 placeholder:text-slate-600 focus:border-blue-500 focus:outline-none"
                    />
                  </label>
                  <label className="block">
                    <span className="text-[11px] font-semibold uppercase tracking-widest text-slate-500">Onset Date</span>
                    <div className="relative mt-2">
                      <CalendarDays size={15} className="absolute left-3 top-1/2 -translate-y-1/2 text-slate-600" />
                      <input
                        type="date"
                        value={onsetDate}
                        onChange={event => setOnsetDate(event.target.value)}
                        className="w-full rounded-lg border border-[#334155] bg-[#0f1117] py-2.5 pl-9 pr-3 text-sm text-slate-100 focus:border-blue-500 focus:outline-none"
                      />
                    </div>
                  </label>
                  <button
                    onClick={() => assess.mutate()}
                    disabled={!complaint.trim() || assess.isPending}
                    className="inline-flex w-full items-center justify-center gap-2 rounded-lg bg-blue-600 px-4 py-2.5 text-sm font-semibold text-white hover:bg-blue-500 disabled:opacity-40"
                  >
                    {assess.isPending ? <Loader2 size={15} className="animate-spin" /> : <ClipboardCheck size={15} />}
                    Assess ADR
                  </button>
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
              </div>
            </aside>

            <main className="space-y-4">
              <div className="rounded-lg border border-[#1e293b] bg-[#1a1f2e] p-4">
                <div className="flex flex-wrap items-center justify-between gap-3">
                  <div>
                    <h2 className="text-sm font-semibold text-slate-100">Suspected Causes</h2>
                    <p className="mt-1 text-xs text-slate-500">
                      {assess.data ? `${assess.data.suspected_causes.length} cause${assess.data.suspected_causes.length === 1 ? '' : 's'} · ${new Date(assess.data.assessed_at).toLocaleString()}` : 'No assessment yet'}
                    </p>
                  </div>
                  {assess.data && (
                    <span className="rounded-full bg-slate-500/15 px-3 py-1 text-xs font-semibold text-slate-300 ring-1 ring-slate-500/30">
                      {assess.data.model_version}
                    </span>
                  )}
                </div>
                {assess.isError && (
                  <div className="mt-4 flex gap-2 rounded-lg border border-red-500/25 bg-red-500/10 p-3 text-sm text-red-200">
                    <AlertTriangle size={16} className="mt-0.5 flex-shrink-0" />
                    ADR assessment failed.
                  </div>
                )}
                {assess.data?.note && (
                  <p className="mt-4 rounded-lg border border-[#253047] bg-[#0f1117] p-3 text-sm text-slate-400">{assess.data.note}</p>
                )}
                <div className="mt-4 space-y-3">
                  {assess.data?.suspected_causes.map(cause => (
                    <CauseCard key={`${cause.drug}-${cause.reaction}-${cause.causality}`} cause={cause} />
                  ))}
                  {assess.data && assess.data.suspected_causes.length === 0 && (
                    <div className="rounded-lg border border-[#253047] bg-[#0f1117] p-5 text-center text-sm text-slate-500">
                      No deterministic ADR suspects returned.
                    </div>
                  )}
                </div>
                {assess.data && (
                  <div className="mt-4 grid grid-cols-1 gap-3 lg:grid-cols-2">
                    <div className="flex gap-2 rounded-lg border border-blue-500/20 bg-blue-500/10 p-3 text-xs leading-relaxed text-blue-200">
                      <ShieldAlert size={15} className="mt-0.5 flex-shrink-0" />
                      <span>{assess.data.pharmacist_verification_notice}</span>
                    </div>
                    <div className="flex gap-2 rounded-lg border border-amber-500/20 bg-amber-500/10 p-3 text-xs leading-relaxed text-amber-200">
                      <AlertTriangle size={15} className="mt-0.5 flex-shrink-0" />
                      <span>{assess.data.not_a_diagnosis_notice}</span>
                    </div>
                  </div>
                )}
              </div>
            </main>
          </div>
        ) : (
          <div className="rounded-lg border border-dashed border-[#334155] bg-[#1a1f2e] p-8 text-center text-sm text-slate-500">
            Select a patient to assess a possible adverse drug reaction.
          </div>
        )}
      </div>
    </div>
  )
}
