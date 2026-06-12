import { useState } from 'react'
import { useMutation } from '@tanstack/react-query'
import {
  AlertTriangle, BookOpen, CheckCircle2, ChevronDown, ChevronRight, Cpu,
  ExternalLink, FileText, Loader2, Pill, Search, ShieldCheck,
} from 'lucide-react'
import {
  clinicalApi,
  type DrugMonographResponse,
  type DrugMonographSection,
  type SecondBrainSource,
} from '../lib/api'

const confidenceClass: Record<DrugMonographSection['confidence'], string> = {
  high: 'bg-emerald-500/15 text-emerald-300 ring-emerald-500/30',
  moderate: 'bg-blue-500/15 text-blue-300 ring-blue-500/30',
  low: 'bg-amber-500/15 text-amber-300 ring-amber-500/30',
  none: 'bg-slate-500/15 text-slate-300 ring-slate-500/30',
}

function SectionBadge({ section }: { section: DrugMonographSection }) {
  if (section.refused) {
    return (
      <span className="inline-flex items-center gap-1.5 rounded-full bg-amber-500/15 px-2.5 py-1 text-[11px] font-semibold text-amber-300 ring-1 ring-amber-500/30">
        <AlertTriangle size={13} />
        no sources
      </span>
    )
  }
  return (
    <span className="inline-flex items-center gap-1.5 rounded-full bg-slate-500/15 px-2.5 py-1 text-[11px] font-semibold text-slate-300 ring-1 ring-slate-500/30">
      {section.llm_used ? <CheckCircle2 size={13} /> : <FileText size={13} />}
      {section.llm_used ? 'LLM-synthesized' : 'extractive'}
    </span>
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

function MonographCard({ section, defaultOpen }: { section: DrugMonographSection; defaultOpen: boolean }) {
  const [open, setOpen] = useState(defaultOpen)

  return (
    <section className="rounded-lg border border-[#1e293b] bg-[#1a1f2e]">
      <button
        type="button"
        onClick={() => setOpen(value => !value)}
        className="flex w-full items-center justify-between gap-3 px-4 py-3 text-left hover:bg-white/[0.03]"
      >
        <div className="flex min-w-0 items-center gap-2.5">
          {open ? <ChevronDown size={16} className="flex-shrink-0 text-slate-500" /> : <ChevronRight size={16} className="flex-shrink-0 text-slate-500" />}
          <h2 className="truncate text-sm font-semibold text-slate-100">{section.label}</h2>
        </div>
        <div className="flex flex-shrink-0 flex-wrap items-center justify-end gap-2">
          <span className={`inline-flex items-center gap-1.5 rounded-full px-2.5 py-1 text-[11px] font-semibold ring-1 ${confidenceClass[section.confidence]}`}>
            <ShieldCheck size={13} />
            {section.confidence}
          </span>
          <SectionBadge section={section} />
        </div>
      </button>

      {open && (
        <div className="space-y-4 border-t border-[#1e293b] p-4">
          <p className="whitespace-pre-wrap text-sm leading-relaxed text-slate-100">{section.answer}</p>
          {section.unsupported && (
            <div className="rounded-lg border border-orange-500/25 bg-orange-500/10 p-3 text-xs text-orange-200">
              LLM synthesis failed grounding checks; extractive source review is shown.
            </div>
          )}
          <div>
            <div className="mb-2 flex items-center justify-between gap-3">
              <p className="text-[11px] font-semibold uppercase tracking-widest text-slate-500">Sources</p>
              <span className="text-xs text-slate-500">{section.sources.length} retrieved</span>
            </div>
            <div className="space-y-3">
              {section.sources.length > 0
                ? section.sources.map(source => <SourceRow key={`${section.key}-${source.source_id}-${source.snippet}`} source={source} />)
                : <p className="rounded-lg border border-[#253047] bg-[#111722] p-3 text-sm text-slate-500">No supporting sources in the knowledge base for this section.</p>}
            </div>
          </div>
        </div>
      )}
    </section>
  )
}

function ResultSummary({ result }: { result: DrugMonographResponse }) {
  const refusedCount = result.sections.filter(section => section.refused).length
  return (
    <div className="flex flex-wrap items-center gap-2">
      <span className="inline-flex items-center gap-1.5 rounded-full bg-emerald-500/15 px-2.5 py-1 text-[11px] font-semibold text-emerald-300 ring-1 ring-emerald-500/30">
        <Cpu size={13} />
        offline / local-only
      </span>
      <span className="inline-flex items-center gap-1.5 rounded-full bg-slate-500/15 px-2.5 py-1 text-[11px] font-semibold text-slate-300 ring-1 ring-slate-500/30">
        {result.llm_used ? <CheckCircle2 size={13} /> : <FileText size={13} />}
        {result.llm_used ? 'local synthesis used' : 'extractive fallback'}
      </span>
      {refusedCount > 0 && (
        <span className="inline-flex items-center gap-1.5 rounded-full bg-amber-500/15 px-2.5 py-1 text-[11px] font-semibold text-amber-300 ring-1 ring-amber-500/30">
          <AlertTriangle size={13} />
          {refusedCount} refused
        </span>
      )}
    </div>
  )
}

export default function DrugIntelligence() {
  const [drugName, setDrugName] = useState('')
  const [topK, setTopK] = useState(6)

  const request = useMutation({
    mutationFn: () => clinicalApi
      .getDrugMonograph({ drug_name: drugName.trim(), top_k: topK })
      .then(r => r.data as DrugMonographResponse),
  })

  const result = request.data
  const canRun = drugName.trim().length > 0 && !request.isPending
  const allRefused = result ? result.sections.every(section => section.refused) : false

  return (
    <div className="h-full overflow-y-auto bg-[#0f1117] p-5">
      <div className="mx-auto max-w-7xl space-y-5">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div>
            <h1 className="text-xl font-semibold text-slate-100">Drug Intelligence</h1>
            <p className="mt-1 text-xs text-slate-500">Offline monograph from ingested clinical references</p>
          </div>
          {result && <ResultSummary result={result} />}
        </div>

        <div className="grid grid-cols-1 gap-5 xl:grid-cols-[360px_1fr]">
          <aside className="space-y-5">
            <section className="rounded-lg border border-[#1e293b] bg-[#1a1f2e] p-4">
              <div className="flex items-center gap-2">
                <Pill size={17} className="text-blue-300" />
                <h2 className="text-sm font-semibold text-slate-100">Reference Drug</h2>
              </div>
              <div className="mt-3 space-y-3">
                <div className="relative">
                  <Search size={14} className="absolute left-3 top-1/2 -translate-y-1/2 text-slate-600" />
                  <input
                    value={drugName}
                    onChange={event => setDrugName(event.target.value)}
                    onKeyDown={event => {
                      if (event.key === 'Enter' && canRun) request.mutate()
                    }}
                    placeholder="Enter drug name"
                    className="w-full rounded-lg border border-[#334155] bg-[#0f1117] py-2.5 pl-8 pr-3 text-sm text-slate-100 placeholder:text-slate-600 focus:border-blue-500 focus:outline-none"
                  />
                </div>
                <div className="flex items-center justify-between gap-3">
                  <label className="text-xs font-medium text-slate-500" htmlFor="drug-intel-top-k">Retrieved sources</label>
                  <select
                    id="drug-intel-top-k"
                    value={topK}
                    onChange={event => setTopK(Number(event.target.value))}
                    className="rounded-lg border border-[#334155] bg-[#0f1117] px-2 py-1.5 text-xs text-slate-100 focus:border-blue-500 focus:outline-none"
                  >
                    {[3, 4, 6, 8, 10, 12].map(value => (
                      <option key={value} value={value}>Top {value}</option>
                    ))}
                  </select>
                </div>
                <button
                  type="button"
                  onClick={() => request.mutate()}
                  disabled={!canRun}
                  className="inline-flex w-full items-center justify-center gap-2 rounded-lg bg-blue-600 px-4 py-2.5 text-sm font-medium text-white hover:bg-blue-500 disabled:opacity-40"
                >
                  {request.isPending ? <Loader2 size={15} className="animate-spin" /> : <BookOpen size={15} />}
                  Build Monograph
                </button>
              </div>
              {request.error && (
                <div className="mt-3 rounded-lg border border-red-500/25 bg-red-500/10 p-3 text-xs text-red-200">
                  Drug Intelligence request failed. Verify the API is reachable and try again.
                </div>
              )}
            </section>

            <section className="rounded-lg border border-emerald-500/20 bg-emerald-500/10 p-4">
              <div className="flex items-center gap-2 text-emerald-200">
                <Cpu size={16} />
                <p className="text-sm font-semibold">Offline / local-only</p>
              </div>
              <p className="mt-2 text-xs leading-relaxed text-emerald-100/80">
                Synthesis is limited to local models when present; otherwise source snippets are returned directly.
              </p>
            </section>
          </aside>

          <main className="space-y-5">
            {result && (
              <section className="rounded-lg border border-[#1e293b] bg-[#1a1f2e] p-4">
                <div className="flex flex-wrap items-start justify-between gap-3">
                  <div>
                    <p className="text-[11px] font-semibold uppercase tracking-widest text-slate-500">Monograph</p>
                    <h2 className="mt-1 text-lg font-semibold text-slate-100">{result.drug_name}</h2>
                    <p className="mt-1 text-xs text-slate-500">Normalized as {result.normalized_name || 'unavailable'} · {result.model_version}</p>
                  </div>
                  <ResultSummary result={result} />
                </div>
                {allRefused && (
                  <div className="mt-4 rounded-lg border border-amber-500/25 bg-amber-500/10 p-3 text-sm text-amber-100">
                    No supporting sources were found for any requested section.
                  </div>
                )}
                <div className="mt-4 grid gap-3 md:grid-cols-2">
                  <div className="rounded-lg border border-blue-500/20 bg-blue-500/10 p-3 text-xs leading-relaxed text-blue-200">
                    {result.pharmacist_verification_notice}
                  </div>
                  <div className="rounded-lg border border-slate-500/20 bg-slate-500/10 p-3 text-xs leading-relaxed text-slate-300">
                    {result.trainable_note}
                  </div>
                </div>
              </section>
            )}

            {result
              ? result.sections.map((section, index) => (
                  <MonographCard key={section.key} section={section} defaultOpen={index < 3} />
                ))
              : (
                  <section className="rounded-lg border border-[#1e293b] bg-[#1a1f2e] p-8 text-center">
                    <Pill size={30} className="mx-auto text-slate-600" />
                    <p className="mt-3 text-sm font-semibold text-slate-200">No monograph loaded</p>
                    <p className="mt-1 text-xs text-slate-500">Enter a drug name to retrieve grounded sections from the local knowledge base.</p>
                  </section>
                )}
          </main>
        </div>
      </div>
    </div>
  )
}
