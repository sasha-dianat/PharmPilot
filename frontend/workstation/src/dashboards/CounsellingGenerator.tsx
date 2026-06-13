import { useState } from 'react'
import type { ReactNode } from 'react'
import { useMutation, useQuery } from '@tanstack/react-query'
import {
  AlertTriangle, CheckSquare, ClipboardList, Copy, Languages, Loader2,
  Pill, Printer, Search, ShieldAlert,
} from 'lucide-react'
import { clinicalApi, patientApi, rxApi } from '../lib/api'

type Level = 'professional' | 'standard' | 'low_literacy' | 'elderly' | 'caregiver'
type Language = 'en' | 'fr' | 'fa' | 'ar' | 'es'

interface Patient {
  id: string
  first_name: string
  last_name: string
  date_of_birth?: string
  gender?: string
}

interface CounsellingContent {
  what_for: string
  how_to_take: string
  what_to_avoid: string[]
  common_side_effects: string[]
  serious_red_flags: string[]
  missed_dose: string
  adherence_tips: string[]
  teach_back_questions: string[]
  level: Level
  language: Language
}

interface CounsellingResponse {
  available: boolean
  drug_name: string
  level: Level
  language: Language
  content: CounsellingContent | null
  llm_used: boolean
  degraded: boolean
  pharmacist_verification_notice: string
  note?: string
}

const LEVELS: Array<{ value: Level; label: string }> = [
  { value: 'standard', label: 'Standard' },
  { value: 'low_literacy', label: 'Low literacy' },
  { value: 'elderly', label: 'Elderly' },
  { value: 'caregiver', label: 'Caregiver' },
  { value: 'professional', label: 'Professional' },
]

const LANGUAGES: Array<{ value: Language; label: string }> = [
  { value: 'en', label: 'English' },
  { value: 'es', label: 'Spanish' },
  { value: 'fr', label: 'French' },
  { value: 'fa', label: 'Farsi' },
  { value: 'ar', label: 'Arabic' },
]

function SectionCard({ title, children, tone = 'slate' }: {
  title: string
  children: ReactNode
  tone?: 'slate' | 'red'
}) {
  return (
    <section className={`rounded-lg border p-4 ${tone === 'red' ? 'border-red-500/30 bg-red-500/10' : 'border-[#1e293b] bg-[#171c29]'}`}>
      <p className={`text-[11px] font-semibold uppercase tracking-widest ${tone === 'red' ? 'text-red-300' : 'text-slate-500'}`}>{title}</p>
      <div className="mt-2 text-sm leading-relaxed text-slate-200">{children}</div>
    </section>
  )
}

function ListSection({ title, items, tone = 'slate', checklist = false }: {
  title: string
  items: string[]
  tone?: 'slate' | 'red'
  checklist?: boolean
}) {
  return (
    <SectionCard title={title} tone={tone}>
      <ul className="space-y-1.5">
        {items.map(item => (
          <li key={item} className="flex gap-2">
            {checklist && <CheckSquare size={14} className="mt-0.5 flex-shrink-0 text-slate-500" />}
            <span>{item}</span>
          </li>
        ))}
      </ul>
    </SectionCard>
  )
}

function printableText(result: CounsellingResponse) {
  if (!result.content) return result.note || ''
  const c = result.content
  return [
    `Counselling: ${result.drug_name}`,
    '',
    `What it is for: ${c.what_for}`,
    `How to take: ${c.how_to_take}`,
    `What to avoid: ${c.what_to_avoid.join('; ')}`,
    `Common side effects: ${c.common_side_effects.join('; ')}`,
    `Serious red flags: ${c.serious_red_flags.join('; ')}`,
    `Missed dose: ${c.missed_dose}`,
    `Adherence tips: ${c.adherence_tips.join('; ')}`,
    `Teach-back questions: ${c.teach_back_questions.join('; ')}`,
    '',
    result.pharmacist_verification_notice,
  ].join('\n')
}

export default function CounsellingGenerator() {
  const [drugName, setDrugName] = useState('')
  const [level, setLevel] = useState<Level>('standard')
  const [language, setLanguage] = useState<Language>('en')
  const [query, setQuery] = useState('')
  const [submitted, setSubmitted] = useState('')
  const [selected, setSelected] = useState<Patient | null>(null)

  const patientSearch = useQuery({
    queryKey: ['counselling-patient-search', submitted],
    queryFn: () => patientApi.search(submitted).then(r => r.data as Patient[]),
    enabled: submitted.length >= 2,
  })

  const rxHistory = useQuery({
    queryKey: ['counselling-rx-history', selected?.id],
    queryFn: () => rxApi.byPatient(selected!.id, 12).then(r => r.data as Array<{ id: string; drug_name: string; status: string }>),
    enabled: !!selected,
  })

  const generate = useMutation({
    mutationFn: () => clinicalApi.generateCounselling({
      drug_name: drugName.trim(),
      patient_id: selected?.id,
      level,
      language,
    }).then(r => r.data as CounsellingResponse),
  })

  const resultMode = generate.data?.llm_used
    ? 'LLM-assisted'
    : generate.data?.degraded
      ? 'Deterministic English fallback'
      : 'Deterministic English'
  const dir = generate.data?.language === 'fa' || generate.data?.language === 'ar' ? 'rtl' : 'ltr'

  return (
    <div className="h-full overflow-y-auto bg-[#0f1117] p-5">
      <div className="mx-auto max-w-7xl space-y-5">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div>
            <h1 className="text-xl font-semibold text-slate-100">Counselling Generator</h1>
            <p className="mt-1 text-xs text-slate-500">Deterministic drug facts with guarded translation and reading-level adaptation</p>
          </div>
          {generate.data && (
            <div className="rounded-lg border border-[#1e293b] bg-[#171c29] px-3 py-2 text-right">
              <p className="text-[10px] uppercase tracking-widest text-slate-500">Mode</p>
              <p className="text-xs font-mono text-slate-300">{resultMode}</p>
            </div>
          )}
        </div>

        <div className="grid grid-cols-1 gap-5 xl:grid-cols-[380px_1fr]">
          <aside className="space-y-5">
            <div className="rounded-lg border border-[#1e293b] bg-[#1a1f2e] p-4">
              <div className="flex items-center gap-2">
                <Pill size={18} className="text-blue-300" />
                <h2 className="text-sm font-semibold text-slate-100">Drug</h2>
              </div>
              <div className="mt-4 space-y-3">
                <label className="block">
                  <span className="text-[11px] font-semibold uppercase tracking-widest text-slate-500">Drug Name</span>
                  <input
                    value={drugName}
                    onChange={event => setDrugName(event.target.value)}
                    placeholder="e.g. lisinopril"
                    className="mt-2 w-full rounded-lg border border-[#334155] bg-[#0f1117] px-3 py-2.5 text-sm text-slate-100 placeholder:text-slate-600 focus:border-blue-500 focus:outline-none"
                  />
                </label>
                <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 xl:grid-cols-1">
                  <label className="block">
                    <span className="text-[11px] font-semibold uppercase tracking-widest text-slate-500">Level</span>
                    <select
                      value={level}
                      onChange={event => setLevel(event.target.value as Level)}
                      className="mt-2 w-full rounded-lg border border-[#334155] bg-[#0f1117] px-3 py-2.5 text-sm text-slate-100 focus:border-blue-500 focus:outline-none"
                    >
                      {LEVELS.map(option => <option key={option.value} value={option.value}>{option.label}</option>)}
                    </select>
                  </label>
                  <label className="block">
                    <span className="text-[11px] font-semibold uppercase tracking-widest text-slate-500">Language</span>
                    <select
                      value={language}
                      onChange={event => setLanguage(event.target.value as Language)}
                      className="mt-2 w-full rounded-lg border border-[#334155] bg-[#0f1117] px-3 py-2.5 text-sm text-slate-100 focus:border-blue-500 focus:outline-none"
                    >
                      {LANGUAGES.map(option => <option key={option.value} value={option.value}>{option.label}</option>)}
                    </select>
                  </label>
                </div>
                <button
                  onClick={() => generate.mutate()}
                  disabled={!drugName.trim() || generate.isPending}
                  className="inline-flex w-full items-center justify-center gap-2 rounded-lg bg-blue-600 px-4 py-2.5 text-sm font-semibold text-white hover:bg-blue-500 disabled:opacity-40"
                >
                  {generate.isPending ? <Loader2 size={15} className="animate-spin" /> : <Languages size={15} />}
                  Generate
                </button>
              </div>
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
                    placeholder="Patient meds"
                    className="w-full rounded-lg border border-[#334155] bg-[#0f1117] py-2.5 pl-9 pr-3 text-sm text-slate-100 placeholder:text-slate-600 focus:border-blue-500 focus:outline-none"
                  />
                </div>
                <button
                  onClick={() => setSubmitted(query.trim())}
                  disabled={query.trim().length < 2 || patientSearch.isFetching}
                  className="inline-flex items-center gap-2 rounded-lg bg-slate-700 px-3 py-2.5 text-sm font-medium text-white hover:bg-slate-600 disabled:opacity-40"
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
                        setSelected(patient)
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
              {selected && (
                <div className="mt-3 space-y-2">
                  {(rxHistory.data ?? []).slice(0, 8).map(rx => (
                    <button
                      key={rx.id}
                      onClick={() => setDrugName(rx.drug_name)}
                      className="flex w-full items-center justify-between rounded bg-[#0f1117] px-3 py-2 text-left hover:bg-white/[0.04]"
                    >
                      <span className="truncate text-xs font-medium text-slate-300">{rx.drug_name}</span>
                      <span className="text-[11px] capitalize text-slate-600">{rx.status?.replace(/_/g, ' ')}</span>
                    </button>
                  ))}
                </div>
              )}
            </div>
          </aside>

          <main className="space-y-4" dir={dir}>
            <div className="rounded-lg border border-[#1e293b] bg-[#1a1f2e] p-4">
              <div className="flex flex-wrap items-center justify-between gap-3">
                <div>
                  <h2 className="text-sm font-semibold text-slate-100">Counselling Output</h2>
                  <p className="mt-1 text-xs text-slate-500">{generate.data ? `${generate.data.drug_name} · ${resultMode}` : 'No counselling generated yet'}</p>
                </div>
                {generate.data && (
                  <div className="flex gap-2">
                    <button
                      onClick={() => navigator.clipboard?.writeText(printableText(generate.data!))}
                      className="inline-flex items-center gap-2 rounded-lg border border-[#334155] bg-[#0f1117] px-3 py-2 text-xs font-medium text-slate-300 hover:bg-white/[0.04]"
                    >
                      <Copy size={14} />
                      Copy
                    </button>
                    <button
                      onClick={() => window.print()}
                      className="inline-flex items-center gap-2 rounded-lg border border-[#334155] bg-[#0f1117] px-3 py-2 text-xs font-medium text-slate-300 hover:bg-white/[0.04]"
                    >
                      <Printer size={14} />
                      Print
                    </button>
                  </div>
                )}
              </div>

              {generate.isError && (
                <div className="mt-4 flex gap-2 rounded-lg border border-red-500/25 bg-red-500/10 p-3 text-sm text-red-200">
                  <AlertTriangle size={16} className="mt-0.5 flex-shrink-0" />
                  Counselling generation failed.
                </div>
              )}
              {generate.data?.note && (
                <p className="mt-4 rounded-lg border border-[#253047] bg-[#0f1117] p-3 text-sm text-slate-400">{generate.data.note}</p>
              )}
              {generate.data?.pharmacist_verification_notice && (
                <div className="mt-4 flex gap-2 rounded-lg border border-blue-500/20 bg-blue-500/10 p-3 text-xs leading-relaxed text-blue-200">
                  <ShieldAlert size={15} className="mt-0.5 flex-shrink-0" />
                  <span>{generate.data.pharmacist_verification_notice}</span>
                </div>
              )}

              {generate.data?.content ? (
                <div className="mt-4 grid grid-cols-1 gap-3 xl:grid-cols-2">
                  <SectionCard title="What It Is For">{generate.data.content.what_for}</SectionCard>
                  <SectionCard title="How To Take">{generate.data.content.how_to_take}</SectionCard>
                  <ListSection title="What To Avoid" items={generate.data.content.what_to_avoid} />
                  <ListSection title="Common Side Effects" items={generate.data.content.common_side_effects} />
                  <ListSection title="Serious Red Flags" items={generate.data.content.serious_red_flags} tone="red" />
                  <SectionCard title="Missed Dose">{generate.data.content.missed_dose}</SectionCard>
                  <ListSection title="Adherence Tips" items={generate.data.content.adherence_tips} />
                  <ListSection title="Teach-Back Questions" items={generate.data.content.teach_back_questions} checklist />
                </div>
              ) : (
                <div className="mt-4 rounded-lg border border-dashed border-[#334155] bg-[#0f1117] p-8 text-center text-sm text-slate-500">
                  <ClipboardList size={24} className="mx-auto mb-2 text-slate-600" />
                  Enter a supported drug to generate structured counselling.
                </div>
              )}
            </div>
          </main>
        </div>
      </div>
    </div>
  )
}
