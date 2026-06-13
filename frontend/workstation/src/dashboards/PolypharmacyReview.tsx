import { useEffect, useMemo, useState } from 'react'
import { useMutation, useQuery } from '@tanstack/react-query'
import {
  AlertTriangle, CheckSquare, Clipboard, Copy, FlaskConical, Loader2,
  MessageSquareText, Search, ShieldAlert, Stethoscope,
} from 'lucide-react'
import { clinicalApi, patientApi, rxApi } from '../lib/api'

interface Patient {
  id: string
  first_name: string
  last_name: string
  date_of_birth?: string
  gender?: string
  conditions?: string[]
}

interface Finding {
  category: string
  priority: 'high' | 'moderate' | 'low'
  drugs_involved: string[]
  explanation: string
  suggested_pharmacist_discussion: string
  tapering_caution: string | null
  evidence_sources: string[]
  confidence: number
  missing_information: string[]
}

interface BurdenScore {
  score: number
  drugs: string[]
}

interface PolyResponse {
  patient_id: string
  reviewed_at: string
  model_version: string
  anticholinergic_burden: BurdenScore
  sedative_fall_risk: BurdenScore
  findings: Finding[]
  physician_message_draft: string
  missing_information_summary: string[]
  pharmacist_verification_notice: string
  note?: string
}

type MessageFormat = 'sbar' | 'concise'

const priorityClass: Record<Finding['priority'], string> = {
  high: 'bg-red-500/15 text-red-300 ring-red-500/30',
  moderate: 'bg-amber-500/15 text-amber-300 ring-amber-500/30',
  low: 'bg-slate-500/15 text-slate-300 ring-slate-500/30',
}

function calcAge(dob?: string): number | null {
  if (!dob) return null
  return Math.floor((Date.now() - new Date(dob).getTime()) / (365.25 * 24 * 3600 * 1000))
}

function Gauge({ title, burden, highAt, moderateAt }: {
  title: string
  burden: BurdenScore
  highAt: number
  moderateAt: number
}) {
  const tone = burden.score >= highAt ? 'red' : burden.score >= moderateAt ? 'amber' : 'emerald'
  const ring = tone === 'red'
    ? 'border-red-500/30 bg-red-500/10 text-red-200'
    : tone === 'amber'
      ? 'border-amber-500/30 bg-amber-500/10 text-amber-200'
      : 'border-emerald-500/25 bg-emerald-500/10 text-emerald-200'
  return (
    <div className={`rounded-lg border p-4 ${ring}`}>
      <p className="text-[11px] font-semibold uppercase tracking-widest opacity-80">{title}</p>
      <div className="mt-3 flex items-end justify-between gap-3">
        <span className="text-4xl font-semibold tabular-nums">{burden.score}</span>
        <span className="pb-1 text-xs opacity-80">{burden.drugs.length} drug{burden.drugs.length === 1 ? '' : 's'}</span>
      </div>
      {burden.drugs.length > 0 && (
        <div className="mt-3 flex flex-wrap gap-1.5">
          {burden.drugs.map(drug => (
            <span key={drug} className="rounded bg-black/20 px-2 py-1 text-[11px]">{drug}</span>
          ))}
        </div>
      )}
    </div>
  )
}

function FindingCard({ finding }: { finding: Finding }) {
  return (
    <div className="rounded-lg border border-[#1e293b] bg-[#171c29] p-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-2">
            <span className="rounded bg-[#0f1117] px-2 py-1 text-[11px] font-semibold uppercase tracking-wide text-slate-400">
              {finding.category.replace(/_/g, ' ')}
            </span>
            <span className={`rounded-full px-2 py-0.5 text-[11px] font-bold capitalize ring-1 ${priorityClass[finding.priority]}`}>
              {finding.priority}
            </span>
            <span className="rounded bg-[#0f1117] px-2 py-1 text-[11px] font-mono text-slate-300">
              {Math.round(finding.confidence * 100)}%
            </span>
          </div>
          <p className="mt-3 text-sm leading-relaxed text-slate-300">{finding.explanation}</p>
        </div>
        <Clipboard size={18} className="text-blue-300" />
      </div>

      {finding.drugs_involved.length > 0 && (
        <div className="mt-4 flex flex-wrap gap-2">
          {finding.drugs_involved.map(drug => (
            <span key={drug} className="rounded bg-slate-800 px-2 py-1 text-[11px] text-slate-300">{drug}</span>
          ))}
        </div>
      )}

      <div className="mt-4 rounded-lg border border-[#253047] bg-[#0f1117] p-3">
        <p className="text-[11px] font-semibold uppercase tracking-widest text-slate-500">Discussion</p>
        <p className="mt-2 text-xs leading-relaxed text-slate-300">{finding.suggested_pharmacist_discussion}</p>
      </div>

      {finding.tapering_caution && (
        <div className="mt-3 flex gap-2 rounded-lg border border-amber-500/30 bg-amber-500/10 p-3 text-xs leading-relaxed text-amber-200">
          <AlertTriangle size={15} className="mt-0.5 flex-shrink-0" />
          <span>{finding.tapering_caution}</span>
        </div>
      )}

      {finding.missing_information.length > 0 && (
        <div className="mt-3 rounded-lg border border-amber-500/25 bg-amber-500/10 p-3">
          <p className="text-[11px] font-semibold uppercase tracking-widest text-amber-300">Missing Information</p>
          <ul className="mt-2 space-y-1.5">
            {finding.missing_information.map(item => (
              <li key={item} className="flex gap-2 text-xs leading-relaxed text-amber-100">
                <CheckSquare size={13} className="mt-0.5 flex-shrink-0 text-amber-300" />
                <span>{item}</span>
              </li>
            ))}
          </ul>
        </div>
      )}

      <div className="mt-3 rounded-lg border border-[#253047] bg-[#0f1117] p-3">
        <p className="text-[11px] font-semibold uppercase tracking-widest text-slate-500">Evidence Sources</p>
        <div className="mt-2 flex flex-wrap gap-2">
          {finding.evidence_sources.map(source => (
            <span key={source} className="rounded bg-slate-800 px-2 py-1 text-[11px] text-slate-300">{source}</span>
          ))}
        </div>
      </div>
    </div>
  )
}

export default function PolypharmacyReview() {
  const [query, setQuery] = useState('')
  const [submitted, setSubmitted] = useState('')
  const [selected, setSelected] = useState<Patient | null>(null)
  const [messageFormat, setMessageFormat] = useState<MessageFormat>('sbar')
  const [draft, setDraft] = useState('')
  const [copied, setCopied] = useState(false)

  const patientSearch = useQuery({
    queryKey: ['poly-patient-search', submitted],
    queryFn: () => patientApi.search(submitted).then(r => r.data as Patient[]),
    enabled: submitted.length >= 2,
  })

  const rxHistory = useQuery({
    queryKey: ['poly-rx-history', selected?.id],
    queryFn: () => rxApi.byPatient(selected!.id, 12).then(r => r.data as Array<{ id: string; drug_name: string; status: string }>),
    enabled: !!selected,
  })

  const reviewMutation = useMutation({
    mutationFn: ({ patientId, format }: { patientId: string; format: MessageFormat }) =>
      clinicalApi.reviewPolypharmacy(patientId, undefined, format).then(r => r.data as PolyResponse),
  })

  useEffect(() => {
    if (reviewMutation.data?.physician_message_draft) {
      setDraft(reviewMutation.data.physician_message_draft)
      setCopied(false)
    }
  }, [reviewMutation.data?.physician_message_draft])

  const age = calcAge(selected?.date_of_birth)
  const findingsByPriority = useMemo(() => reviewMutation.data?.findings ?? [], [reviewMutation.data?.findings])

  return (
    <div className="h-full overflow-y-auto bg-[#0f1117] p-5">
      <div className="mx-auto max-w-7xl space-y-5">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div>
            <h1 className="text-xl font-semibold text-slate-100">Polypharmacy Review</h1>
            <p className="mt-1 text-xs text-slate-500">Deterministic deprescribing and therapy-gap assistant</p>
          </div>
          {reviewMutation.data && (
            <div className="rounded-lg border border-[#1e293b] bg-[#171c29] px-3 py-2 text-right">
              <p className="text-[10px] uppercase tracking-widest text-slate-500">Model</p>
              <p className="text-xs font-mono text-slate-300">{reviewMutation.data.model_version}</p>
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
                    reviewMutation.reset()
                    setDraft('')
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
                </dl>
                <div className="mt-4">
                  <p className="text-[11px] font-semibold uppercase tracking-widest text-slate-500">Conditions</p>
                  <div className="mt-2 flex flex-wrap gap-2">
                    {(selected.conditions?.length ? selected.conditions : ['None recorded']).map(item => (
                      <span key={item} className="rounded bg-slate-800 px-2 py-1 text-[11px] text-slate-300">{item}</span>
                    ))}
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
                <div className="mt-4 grid grid-cols-2 overflow-hidden rounded-lg border border-[#334155]">
                  {(['sbar', 'concise'] as MessageFormat[]).map(format => (
                    <button
                      key={format}
                      onClick={() => setMessageFormat(format)}
                      className={`px-3 py-2 text-xs font-semibold uppercase tracking-wide ${
                        messageFormat === format ? 'bg-blue-600 text-white' : 'bg-[#0f1117] text-slate-400 hover:text-slate-200'
                      }`}
                    >
                      {format}
                    </button>
                  ))}
                </div>
                <button
                  onClick={() => reviewMutation.mutate({ patientId: selected.id, format: messageFormat })}
                  disabled={reviewMutation.isPending}
                  className="mt-3 inline-flex w-full items-center justify-center gap-2 rounded-lg bg-blue-600 px-4 py-2.5 text-sm font-semibold text-white hover:bg-blue-500 disabled:opacity-40"
                >
                  {reviewMutation.isPending ? <Loader2 size={15} className="animate-spin" /> : <ShieldAlert size={15} />}
                  Review
                </button>
              </div>
            </aside>

            <main className="space-y-4">
              {reviewMutation.data && (
                <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
                  <Gauge title="Anticholinergic ACB" burden={reviewMutation.data.anticholinergic_burden} highAt={3} moderateAt={1} />
                  <Gauge title="Sedative Fall Risk" burden={reviewMutation.data.sedative_fall_risk} highAt={3} moderateAt={2} />
                </div>
              )}

              <div className="rounded-lg border border-[#1e293b] bg-[#1a1f2e] p-4">
                <div className="flex flex-wrap items-center justify-between gap-3">
                  <div>
                    <h2 className="text-sm font-semibold text-slate-100">Findings</h2>
                    <p className="mt-1 text-xs text-slate-500">
                      {reviewMutation.data ? `${reviewMutation.data.findings.length} finding${reviewMutation.data.findings.length === 1 ? '' : 's'} · ${new Date(reviewMutation.data.reviewed_at).toLocaleString()}` : 'No review yet'}
                    </p>
                  </div>
                  {reviewMutation.data?.missing_information_summary.length ? (
                    <span className="rounded-full bg-amber-500/15 px-3 py-1 text-xs font-semibold text-amber-300 ring-1 ring-amber-500/30">
                      Missing data
                    </span>
                  ) : null}
                </div>

                {reviewMutation.isError && (
                  <div className="mt-4 flex gap-2 rounded-lg border border-red-500/25 bg-red-500/10 p-3 text-sm text-red-200">
                    <AlertTriangle size={16} className="mt-0.5 flex-shrink-0" />
                    Polypharmacy review failed.
                  </div>
                )}
                {reviewMutation.data?.note && (
                  <p className="mt-4 rounded-lg border border-[#253047] bg-[#0f1117] p-3 text-sm text-slate-400">{reviewMutation.data.note}</p>
                )}
                {reviewMutation.data?.missing_information_summary.length ? (
                  <div className="mt-4 rounded-lg border border-amber-500/25 bg-amber-500/10 p-3">
                    <p className="text-[11px] font-semibold uppercase tracking-widest text-amber-300">Missing Information</p>
                    <div className="mt-2 flex flex-wrap gap-2">
                      {reviewMutation.data.missing_information_summary.map(item => (
                        <span key={item} className="rounded bg-amber-500/15 px-2 py-1 text-xs text-amber-200">{item}</span>
                      ))}
                    </div>
                  </div>
                ) : null}

                <div className="mt-4 space-y-3">
                  {findingsByPriority.map(item => (
                    <FindingCard key={`${item.category}-${item.drugs_involved.join('-')}-${item.explanation}`} finding={item} />
                  ))}
                  {reviewMutation.data && reviewMutation.data.findings.length === 0 && (
                    <div className="rounded-lg border border-[#253047] bg-[#0f1117] p-5 text-center text-sm text-slate-500">
                      No deterministic polypharmacy findings returned.
                    </div>
                  )}
                </div>
              </div>

              {reviewMutation.data && (
                <div className="rounded-lg border border-[#1e293b] bg-[#1a1f2e] p-4">
                  <div className="flex flex-wrap items-center justify-between gap-3">
                    <div className="flex items-center gap-2">
                      <MessageSquareText size={17} className="text-blue-300" />
                      <h2 className="text-sm font-semibold text-slate-100">Physician Message Draft</h2>
                    </div>
                    <button
                      onClick={() => {
                        navigator.clipboard.writeText(draft)
                        setCopied(true)
                      }}
                      className="inline-flex items-center gap-2 rounded-lg border border-[#334155] px-3 py-2 text-xs font-semibold text-slate-300 hover:bg-white/[0.04]"
                    >
                      <Copy size={14} />
                      {copied ? 'Copied' : 'Copy'}
                    </button>
                  </div>
                  <textarea
                    value={draft}
                    onChange={event => setDraft(event.target.value)}
                    rows={13}
                    className="mt-3 w-full resize-y rounded-lg border border-[#334155] bg-[#0f1117] px-3 py-2.5 font-mono text-xs leading-relaxed text-slate-100 focus:border-blue-500 focus:outline-none"
                  />
                  <div className="mt-4 flex gap-2 rounded-lg border border-blue-500/20 bg-blue-500/10 p-3 text-xs leading-relaxed text-blue-200">
                    <ShieldAlert size={15} className="mt-0.5 flex-shrink-0" />
                    <span>{reviewMutation.data.pharmacist_verification_notice}</span>
                  </div>
                </div>
              )}
            </main>
          </div>
        ) : (
          <div className="rounded-lg border border-dashed border-[#334155] bg-[#1a1f2e] p-8 text-center text-sm text-slate-500">
            Select a patient to run a deterministic polypharmacy review.
          </div>
        )}
      </div>
    </div>
  )
}
