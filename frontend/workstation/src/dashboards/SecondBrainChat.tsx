import { useState } from 'react'
import { useMutation, useQuery } from '@tanstack/react-query'
import {
  AlertTriangle, BookOpen, CheckCircle2, ExternalLink, FileText, Loader2,
  MessageSquare, Search, ShieldCheck, UserRound, X,
} from 'lucide-react'
import { clinicalApi, patientApi, type SecondBrainResponse, type SecondBrainSource } from '../lib/api'

interface Patient {
  id: string
  first_name: string
  last_name: string
  date_of_birth?: string
  gender?: string
}

const confidenceClass: Record<SecondBrainResponse['confidence'], string> = {
  high: 'bg-emerald-500/15 text-emerald-300 ring-emerald-500/30',
  moderate: 'bg-blue-500/15 text-blue-300 ring-blue-500/30',
  low: 'bg-amber-500/15 text-amber-300 ring-amber-500/30',
  none: 'bg-slate-500/15 text-slate-300 ring-slate-500/30',
}

function StatusBadges({ result }: { result: SecondBrainResponse }) {
  return (
    <div className="flex flex-wrap gap-2">
      {result.refused && (
        <span className="inline-flex items-center gap-1.5 rounded-full bg-amber-500/15 px-2.5 py-1 text-[11px] font-semibold text-amber-300 ring-1 ring-amber-500/30">
          <AlertTriangle size={13} />
          No supporting sources found
        </span>
      )}
      {result.unsupported && (
        <span className="inline-flex items-center gap-1.5 rounded-full bg-orange-500/15 px-2.5 py-1 text-[11px] font-semibold text-orange-300 ring-1 ring-orange-500/30">
          <AlertTriangle size={13} />
          Unsupported synthesis
        </span>
      )}
      <span className={`inline-flex items-center gap-1.5 rounded-full px-2.5 py-1 text-[11px] font-semibold ring-1 ${confidenceClass[result.confidence]}`}>
        <ShieldCheck size={13} />
        {result.confidence} confidence
      </span>
      <span className="inline-flex items-center gap-1.5 rounded-full bg-slate-500/15 px-2.5 py-1 text-[11px] font-semibold text-slate-300 ring-1 ring-slate-500/30">
        {result.llm_used ? <CheckCircle2 size={13} /> : <FileText size={13} />}
        {result.llm_used ? 'AI synthesized' : 'Extractive sources only'}
      </span>
    </div>
  )
}

function SourceRow({ source }: { source: SecondBrainSource }) {
  return (
    <div className="rounded-lg border border-[#253047] bg-[#111722] p-3">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <p className="truncate text-xs font-semibold text-slate-100">{source.source_title || 'Unknown source'}</p>
          <div className="mt-1 flex flex-wrap items-center gap-2 text-[10px] text-slate-500">
            <span className="font-mono text-blue-300">{source.source_id}</span>
            <span>{source.source_type || 'reference'}</span>
            {source.evidence_grade && <span>grade {source.evidence_grade}</span>}
            <span>{Math.round(source.similarity_score * 100)}% match</span>
          </div>
        </div>
        {source.url && (
          <a
            href={source.url}
            target="_blank"
            rel="noreferrer"
            className="inline-flex h-8 w-8 flex-shrink-0 items-center justify-center rounded-lg text-slate-400 hover:bg-white/[0.05] hover:text-slate-100"
            title="Open source"
          >
            <ExternalLink size={15} />
          </a>
        )}
      </div>
      <p className="mt-2 text-xs leading-relaxed text-slate-400">{source.snippet}</p>
    </div>
  )
}

export default function SecondBrainChat() {
  const [question, setQuestion] = useState('')
  const [patientQuery, setPatientQuery] = useState('')
  const [submittedPatientQuery, setSubmittedPatientQuery] = useState('')
  const [selectedPatient, setSelectedPatient] = useState<Patient | null>(null)
  const [topK, setTopK] = useState(8)

  const patientSearch = useQuery({
    queryKey: ['second-brain-patient-search', submittedPatientQuery],
    queryFn: () => patientApi.search(submittedPatientQuery).then(r => r.data as Patient[]),
    enabled: submittedPatientQuery.length >= 2,
  })

  const ask = useMutation({
    mutationFn: () => clinicalApi
      .querySecondBrain(question.trim(), selectedPatient?.id, topK)
      .then(r => r.data as SecondBrainResponse),
  })

  const result = ask.data
  const canAsk = question.trim().length > 0 && !ask.isPending

  return (
    <div className="h-full overflow-y-auto bg-[#0f1117] p-5">
      <div className="mx-auto max-w-7xl space-y-5">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div>
            <h1 className="text-xl font-semibold text-slate-100">Pharmacist Second Brain</h1>
            <p className="mt-1 text-xs text-slate-500">RAG Q&A over ingested clinical references</p>
          </div>
          {result && <StatusBadges result={result} />}
        </div>

        <div className="grid grid-cols-1 gap-5 xl:grid-cols-[360px_1fr]">
          <aside className="space-y-5">
            <section className="rounded-lg border border-[#1e293b] bg-[#1a1f2e] p-4">
              <div className="flex items-center gap-2">
                <UserRound size={17} className="text-blue-300" />
                <h2 className="text-sm font-semibold text-slate-100">Patient</h2>
              </div>
              <div className="mt-3 flex gap-2">
                <div className="relative min-w-0 flex-1">
                  <Search size={14} className="absolute left-3 top-1/2 -translate-y-1/2 text-slate-600" />
                  <input
                    value={patientQuery}
                    onChange={event => setPatientQuery(event.target.value)}
                    onKeyDown={event => {
                      if (event.key === 'Enter' && patientQuery.trim().length >= 2) {
                        setSubmittedPatientQuery(patientQuery.trim())
                      }
                    }}
                    placeholder="Search patient"
                    className="w-full rounded-lg border border-[#334155] bg-[#0f1117] py-2 pl-8 pr-3 text-xs text-slate-100 placeholder:text-slate-600 focus:border-blue-500 focus:outline-none"
                  />
                </div>
                <button
                  onClick={() => setSubmittedPatientQuery(patientQuery.trim())}
                  disabled={patientQuery.trim().length < 2 || patientSearch.isFetching}
                  className="inline-flex h-9 w-9 items-center justify-center rounded-lg bg-blue-600 text-white hover:bg-blue-500 disabled:opacity-40"
                  title="Search patient"
                >
                  {patientSearch.isFetching ? <Loader2 size={15} className="animate-spin" /> : <Search size={15} />}
                </button>
              </div>

              {(patientSearch.data?.length ?? 0) > 0 && (
                <div className="mt-3 overflow-hidden rounded-lg border border-[#253047]">
                  {patientSearch.data!.map(patient => (
                    <button
                      key={patient.id}
                      onClick={() => {
                        setSelectedPatient(patient)
                        setPatientQuery(`${patient.last_name}, ${patient.first_name}`)
                      }}
                      className="flex w-full items-center justify-between gap-3 border-b border-[#253047] px-3 py-2.5 text-left last:border-b-0 hover:bg-white/[0.03]"
                    >
                      <span className="min-w-0 truncate text-xs font-medium text-slate-200">{patient.last_name}, {patient.first_name}</span>
                      <span className="flex-shrink-0 text-[10px] text-slate-500">{patient.date_of_birth || 'DOB unknown'}</span>
                    </button>
                  ))}
                </div>
              )}

              {selectedPatient && (
                <div className="mt-3 rounded-lg border border-blue-500/20 bg-blue-500/10 p-3">
                  <div className="flex items-center justify-between gap-2">
                    <p className="truncate text-xs font-semibold text-blue-100">
                      {selectedPatient.last_name}, {selectedPatient.first_name}
                    </p>
                    <button
                      onClick={() => {
                        setSelectedPatient(null)
                        setPatientQuery('')
                        setSubmittedPatientQuery('')
                      }}
                      className="inline-flex h-7 w-7 flex-shrink-0 items-center justify-center rounded-lg text-blue-200 hover:bg-blue-500/20"
                      title="Clear patient"
                    >
                      <X size={14} />
                    </button>
                  </div>
                </div>
              )}
            </section>

            <section className="rounded-lg border border-[#1e293b] bg-[#1a1f2e] p-4">
              <div className="flex items-center justify-between gap-3">
                <div className="flex items-center gap-2">
                  <BookOpen size={17} className="text-emerald-300" />
                  <h2 className="text-sm font-semibold text-slate-100">Retrieval</h2>
                </div>
                <select
                  value={topK}
                  onChange={event => setTopK(Number(event.target.value))}
                  className="rounded-lg border border-[#334155] bg-[#0f1117] px-2 py-1.5 text-xs text-slate-100 focus:border-blue-500 focus:outline-none"
                >
                  {[4, 6, 8, 10, 12, 16, 20].map(value => (
                    <option key={value} value={value}>Top {value}</option>
                  ))}
                </select>
              </div>
              {result?.patient_context && (
                <div className="mt-4 rounded-lg border border-[#253047] bg-[#0f1117] p-3">
                  <p className="text-[11px] font-semibold uppercase tracking-widest text-slate-500">Patient context</p>
                  <pre className="mt-2 whitespace-pre-wrap text-xs leading-relaxed text-slate-300">{result.patient_context}</pre>
                </div>
              )}
              {!result?.patient_context && (
                <p className="mt-3 text-xs leading-relaxed text-slate-500">
                  Patient facts, when selected, appear here separately from literature-based synthesis.
                </p>
              )}
            </section>
          </aside>

          <main className="space-y-5">
            <section className="rounded-lg border border-[#1e293b] bg-[#1a1f2e] p-4">
              <label className="text-[11px] font-semibold uppercase tracking-widest text-slate-500">Question</label>
              <textarea
                value={question}
                onChange={event => setQuestion(event.target.value)}
                placeholder="Ask a pharmacist reference question"
                rows={4}
                className="mt-2 w-full resize-none rounded-lg border border-[#334155] bg-[#0f1117] p-3 text-sm leading-relaxed text-slate-100 placeholder:text-slate-600 focus:border-blue-500 focus:outline-none"
              />
              <div className="mt-3 flex flex-wrap items-center justify-between gap-3">
                <p className="text-xs text-slate-500">Answers are limited to retrieved source chunks.</p>
                <button
                  onClick={() => ask.mutate()}
                  disabled={!canAsk}
                  className="inline-flex items-center gap-2 rounded-lg bg-blue-600 px-4 py-2.5 text-sm font-medium text-white hover:bg-blue-500 disabled:opacity-40"
                >
                  {ask.isPending ? <Loader2 size={15} className="animate-spin" /> : <MessageSquare size={15} />}
                  Ask
                </button>
              </div>
              {ask.error && (
                <div className="mt-3 rounded-lg border border-red-500/25 bg-red-500/10 p-3 text-xs text-red-200">
                  Second Brain query failed. Verify the API is reachable and try again.
                </div>
              )}
            </section>

            {result && (
              <section className="rounded-lg border border-[#1e293b] bg-[#1a1f2e]">
                <div className="border-b border-[#1e293b] px-4 py-3">
                  <p className="text-[11px] font-semibold uppercase tracking-widest text-slate-500">Answer</p>
                </div>
                <div className="space-y-4 p-4">
                  <p className="whitespace-pre-wrap text-sm leading-relaxed text-slate-100">{result.answer}</p>
                  <div className="rounded-lg border border-blue-500/20 bg-blue-500/10 p-3 text-xs leading-relaxed text-blue-200">
                    {result.pharmacist_verification_notice}
                  </div>
                </div>
              </section>
            )}

            {result && (
              <section className="rounded-lg border border-[#1e293b] bg-[#1a1f2e]">
                <div className="flex items-center justify-between gap-3 border-b border-[#1e293b] px-4 py-3">
                  <p className="text-[11px] font-semibold uppercase tracking-widest text-slate-500">Sources</p>
                  <span className="text-xs text-slate-500">{result.sources.length} retrieved</span>
                </div>
                <div className="space-y-3 p-4">
                  {result.sources.length > 0
                    ? result.sources.map(source => <SourceRow key={`${source.source_id}-${source.snippet}`} source={source} />)
                    : <p className="text-sm text-slate-500">No retrieved sources were available.</p>}
                </div>
              </section>
            )}
          </main>
        </div>
      </div>
    </div>
  )
}
