import { useState } from 'react'
import { useMutation } from '@tanstack/react-query'
import {
  AlertTriangle, ClipboardEdit, Copy, FileText, Languages, Loader2, Plus, Printer, ShieldAlert,
  Trash2,
} from 'lucide-react'
import { clinicalApi } from '../lib/api'

type MessageFormat = 'sbar' | 'soap' | 'concise' | 'letter'
type Language = 'en' | 'fr' | 'fa' | 'ar' | 'es'
type Urgency = 'routine' | 'urgent' | 'emergent'

interface PhysicianMessageResponse {
  format: MessageFormat
  language: Language
  urgency: Urgency
  message: {
    subject: string
    body: string
    sections: Record<string, string>
  }
  llm_used: boolean
  degraded: boolean
  pharmacist_verification_notice: string
  note?: string
}

const FORMATS: Array<{ value: MessageFormat; label: string }> = [
  { value: 'sbar', label: 'SBAR' },
  { value: 'soap', label: 'SOAP' },
  { value: 'concise', label: 'Concise note' },
  { value: 'letter', label: 'Formal letter' },
]

const LANGUAGES: Array<{ value: Language; label: string }> = [
  { value: 'en', label: 'English' },
  { value: 'es', label: 'Spanish' },
  { value: 'fr', label: 'French' },
  { value: 'fa', label: 'Farsi' },
  { value: 'ar', label: 'Arabic' },
]

const URGENCIES: Array<{ value: Urgency; label: string }> = [
  { value: 'routine', label: 'Routine' },
  { value: 'urgent', label: 'Urgent' },
  { value: 'emergent', label: 'Emergent' },
]

function outputText(result: PhysicianMessageResponse | undefined, body: string) {
  if (!result) return ''
  return [
    `Subject: ${result.message.subject}`,
    '',
    body,
    '',
    result.pharmacist_verification_notice,
  ].join('\n')
}

export default function PhysicianMessageComposer() {
  const [prescriberName, setPrescriberName] = useState('')
  const [patientContext, setPatientContext] = useState('')
  const [medicationIssue, setMedicationIssue] = useState('')
  const [clinicalRationale, setClinicalRationale] = useState('')
  const [recommendation, setRecommendation] = useState('')
  const [pharmacistName, setPharmacistName] = useState('')
  const [urgency, setUrgency] = useState<Urgency>('routine')
  const [format, setFormat] = useState<MessageFormat>('sbar')
  const [language, setLanguage] = useState<Language>('en')
  const [supportingData, setSupportingData] = useState<string[]>([''])
  const [editableBody, setEditableBody] = useState('')

  const generate = useMutation({
    mutationFn: () => clinicalApi.generatePhysicianMessage({
      prescriber_name: prescriberName.trim() || undefined,
      patient_context: patientContext.trim() || undefined,
      medication_issue: medicationIssue.trim(),
      clinical_rationale: clinicalRationale.trim() || undefined,
      recommendation_or_question: recommendation.trim(),
      urgency,
      supporting_data: supportingData.map(item => item.trim()).filter(Boolean),
      pharmacist_name: pharmacistName.trim() || undefined,
      format,
      language,
    }).then(r => r.data as PhysicianMessageResponse),
    onSuccess: data => setEditableBody(data.message.body),
  })

  const resultMode = generate.data?.llm_used
    ? 'LLM-assisted translation/tone'
    : generate.data?.degraded
      ? 'Deterministic English fallback'
      : 'Deterministic English'
  const dir = generate.data?.language === 'fa' || generate.data?.language === 'ar' ? 'rtl' : 'ltr'
  const canGenerate = medicationIssue.trim().length > 0 && recommendation.trim().length > 0

  return (
    <div className="h-full overflow-y-auto bg-[#0f1117] p-5">
      <div className="mx-auto max-w-7xl space-y-5">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div>
            <h1 className="text-xl font-semibold text-slate-100">Physician Message Composer</h1>
            <p className="mt-1 text-xs text-slate-500">Pharmacist-authored content with deterministic formatting and guarded translation</p>
          </div>
          {generate.data && (
            <div className="rounded-lg border border-[#1e293b] bg-[#171c29] px-3 py-2 text-right">
              <p className="text-[10px] uppercase tracking-widest text-slate-500">Mode</p>
              <p className="text-xs font-mono text-slate-300">{resultMode}</p>
            </div>
          )}
        </div>

        <div className="grid grid-cols-1 gap-5 xl:grid-cols-[420px_1fr]">
          <aside className="space-y-5">
            <div className="rounded-lg border border-[#1e293b] bg-[#1a1f2e] p-4">
              <div className="flex items-center gap-2">
                <ClipboardEdit size={18} className="text-blue-300" />
                <h2 className="text-sm font-semibold text-slate-100">Message Inputs</h2>
              </div>
              <div className="mt-4 space-y-3">
                <label className="block">
                  <span className="text-[11px] font-semibold uppercase tracking-widest text-slate-500">Prescriber Name</span>
                  <input
                    value={prescriberName}
                    onChange={event => setPrescriberName(event.target.value)}
                    placeholder="e.g. Nguyen"
                    className="mt-2 w-full rounded-lg border border-[#334155] bg-[#0f1117] px-3 py-2.5 text-sm text-slate-100 placeholder:text-slate-600 focus:border-blue-500 focus:outline-none"
                  />
                </label>
                <label className="block">
                  <span className="text-[11px] font-semibold uppercase tracking-widest text-slate-500">Patient Context</span>
                  <textarea
                    value={patientContext}
                    onChange={event => setPatientContext(event.target.value)}
                    rows={3}
                    placeholder="Minimal non-identifying context"
                    className="mt-2 w-full resize-none rounded-lg border border-[#334155] bg-[#0f1117] px-3 py-2.5 text-sm text-slate-100 placeholder:text-slate-600 focus:border-blue-500 focus:outline-none"
                  />
                </label>
                <label className="block">
                  <span className="text-[11px] font-semibold uppercase tracking-widest text-slate-500">Medication Issue *</span>
                  <textarea
                    value={medicationIssue}
                    onChange={event => setMedicationIssue(event.target.value)}
                    rows={4}
                    placeholder="Pharmacist-authored concern"
                    className="mt-2 w-full resize-none rounded-lg border border-[#334155] bg-[#0f1117] px-3 py-2.5 text-sm text-slate-100 placeholder:text-slate-600 focus:border-blue-500 focus:outline-none"
                  />
                </label>
                <label className="block">
                  <span className="text-[11px] font-semibold uppercase tracking-widest text-slate-500">Clinical Rationale</span>
                  <textarea
                    value={clinicalRationale}
                    onChange={event => setClinicalRationale(event.target.value)}
                    rows={3}
                    placeholder="Reasoning supplied by pharmacist"
                    className="mt-2 w-full resize-none rounded-lg border border-[#334155] bg-[#0f1117] px-3 py-2.5 text-sm text-slate-100 placeholder:text-slate-600 focus:border-blue-500 focus:outline-none"
                  />
                </label>
                <label className="block">
                  <span className="text-[11px] font-semibold uppercase tracking-widest text-slate-500">Recommendation / Question *</span>
                  <textarea
                    value={recommendation}
                    onChange={event => setRecommendation(event.target.value)}
                    rows={3}
                    placeholder="What should the prescriber consider or answer?"
                    className="mt-2 w-full resize-none rounded-lg border border-[#334155] bg-[#0f1117] px-3 py-2.5 text-sm text-slate-100 placeholder:text-slate-600 focus:border-blue-500 focus:outline-none"
                  />
                </label>
              </div>
            </div>

            <div className="rounded-lg border border-[#1e293b] bg-[#1a1f2e] p-4">
              <div className="flex items-center gap-2">
                <FileText size={18} className="text-blue-300" />
                <h2 className="text-sm font-semibold text-slate-100">Format</h2>
              </div>
              <div className="mt-4 grid grid-cols-1 gap-3 sm:grid-cols-2 xl:grid-cols-1">
                <label className="block">
                  <span className="text-[11px] font-semibold uppercase tracking-widest text-slate-500">Urgency</span>
                  <select
                    value={urgency}
                    onChange={event => setUrgency(event.target.value as Urgency)}
                    className="mt-2 w-full rounded-lg border border-[#334155] bg-[#0f1117] px-3 py-2.5 text-sm text-slate-100 focus:border-blue-500 focus:outline-none"
                  >
                    {URGENCIES.map(option => <option key={option.value} value={option.value}>{option.label}</option>)}
                  </select>
                </label>
                <label className="block">
                  <span className="text-[11px] font-semibold uppercase tracking-widest text-slate-500">Template</span>
                  <select
                    value={format}
                    onChange={event => setFormat(event.target.value as MessageFormat)}
                    className="mt-2 w-full rounded-lg border border-[#334155] bg-[#0f1117] px-3 py-2.5 text-sm text-slate-100 focus:border-blue-500 focus:outline-none"
                  >
                    {FORMATS.map(option => <option key={option.value} value={option.value}>{option.label}</option>)}
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
                <label className="block">
                  <span className="text-[11px] font-semibold uppercase tracking-widest text-slate-500">Pharmacist Name</span>
                  <input
                    value={pharmacistName}
                    onChange={event => setPharmacistName(event.target.value)}
                    placeholder="Optional sign-off"
                    className="mt-2 w-full rounded-lg border border-[#334155] bg-[#0f1117] px-3 py-2.5 text-sm text-slate-100 placeholder:text-slate-600 focus:border-blue-500 focus:outline-none"
                  />
                </label>
              </div>
            </div>

            <div className="rounded-lg border border-[#1e293b] bg-[#1a1f2e] p-4">
              <div className="flex items-center justify-between gap-2">
                <h2 className="text-sm font-semibold text-slate-100">Supporting Data</h2>
                <button
                  onClick={() => setSupportingData(items => [...items, ''])}
                  className="inline-flex items-center gap-1.5 rounded-lg border border-[#334155] bg-[#0f1117] px-2.5 py-1.5 text-xs font-medium text-slate-300 hover:bg-white/[0.04]"
                >
                  <Plus size={13} />
                  Add
                </button>
              </div>
              <div className="mt-3 space-y-2">
                {supportingData.map((item, index) => (
                  <div key={index} className="flex gap-2">
                    <input
                      value={item}
                      onChange={event => setSupportingData(items => items.map((value, i) => i === index ? event.target.value : value))}
                      placeholder="e.g. eGFR 42 mL/min"
                      className="min-w-0 flex-1 rounded-lg border border-[#334155] bg-[#0f1117] px-3 py-2 text-sm text-slate-100 placeholder:text-slate-600 focus:border-blue-500 focus:outline-none"
                    />
                    <button
                      onClick={() => setSupportingData(items => items.length === 1 ? [''] : items.filter((_, i) => i !== index))}
                      className="inline-flex h-9 w-9 flex-shrink-0 items-center justify-center rounded-lg border border-[#334155] bg-[#0f1117] text-slate-400 hover:bg-white/[0.04]"
                      aria-label="Remove supporting data"
                    >
                      <Trash2 size={14} />
                    </button>
                  </div>
                ))}
              </div>
              <button
                onClick={() => generate.mutate()}
                disabled={!canGenerate || generate.isPending}
                className="mt-4 inline-flex w-full items-center justify-center gap-2 rounded-lg bg-blue-600 px-4 py-2.5 text-sm font-semibold text-white hover:bg-blue-500 disabled:opacity-40"
              >
                {generate.isPending ? <Loader2 size={15} className="animate-spin" /> : <Languages size={15} />}
                Generate
              </button>
            </div>
          </aside>

          <main className="space-y-4" dir={dir}>
            <div className="rounded-lg border border-[#1e293b] bg-[#1a1f2e] p-4">
              <div className="flex flex-wrap items-center justify-between gap-3">
                <div>
                  <h2 className="text-sm font-semibold text-slate-100">Editable Message</h2>
                  <p className="mt-1 text-xs text-slate-500">{generate.data ? `${generate.data.format.toUpperCase()} · ${resultMode}` : 'No message generated yet'}</p>
                </div>
                {generate.data && (
                  <div className="flex gap-2">
                    <button
                      onClick={() => navigator.clipboard?.writeText(outputText(generate.data, editableBody))}
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
                  Physician message generation failed.
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

              {generate.data ? (
                <div className="mt-4 space-y-3">
                  <label className="block">
                    <span className="text-[11px] font-semibold uppercase tracking-widest text-slate-500">Subject</span>
                    <input
                      value={generate.data.message.subject}
                      readOnly
                      className="mt-2 w-full rounded-lg border border-[#334155] bg-[#0f1117] px-3 py-2.5 text-sm text-slate-200 focus:outline-none"
                    />
                  </label>
                  <label className="block">
                    <span className="text-[11px] font-semibold uppercase tracking-widest text-slate-500">Body</span>
                    <textarea
                      value={editableBody}
                      onChange={event => setEditableBody(event.target.value)}
                      rows={22}
                      className="mt-2 w-full resize-y rounded-lg border border-[#334155] bg-[#0f1117] px-3 py-3 font-mono text-sm leading-relaxed text-slate-100 focus:border-blue-500 focus:outline-none"
                    />
                  </label>
                </div>
              ) : (
                <div className="mt-4 rounded-lg border border-dashed border-[#334155] bg-[#0f1117] p-8 text-center text-sm text-slate-500">
                  <FileText size={24} className="mx-auto mb-2 text-slate-600" />
                  Enter pharmacist-authored content to assemble a physician message.
                </div>
              )}
            </div>
          </main>
        </div>
      </div>
    </div>
  )
}
