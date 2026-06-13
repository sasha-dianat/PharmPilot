/**
 * LabelSimplificationPanel — #8 Personalized Label Language Simplification
 * ========================================================================
 * Sits beside the label preview. Shows:
 *   • PATIENT-FACING plain-language instructions (the default to print on the
 *     label) — editable, in the patient's language.
 *   • PHARMACIST-FACING reference dosing retrieved from the trainable corpus,
 *     so the pharmacist can verify the SIG against authoritative dosing.
 *   • An "add reference" affordance to grow the corpus.
 *
 * Consumes the §1.2 envelope; shows a TierBadge.
 */
import { useState, useEffect } from 'react'
import { useMutation } from '@tanstack/react-query'
import { apiClient } from '../lib/api'
import TierBadge from './TierBadge'

interface ReferenceDosing { text: string; source: string; score: number }
interface SimplifyResult {
  expanded_sig: string; patient_instructions: string; language: string
  reference_dosing: ReferenceDosing[]; has_reference: boolean
}
interface Envelope {
  result: SimplifyResult; tier_used: 'local' | 'cloud' | 'hybrid'
  degraded: boolean; confidence: number; options_offline: string[]
}

interface Props {
  sig:         string
  drugName:    string
  language?:   string
  patientAge?: number
  onUseInstructions?: (text: string) => void   // push the simplified text onto the label
}

const LANGUAGES = [
  { code: 'en', name: 'English' }, { code: 'fa', name: 'فارسی' },
  { code: 'ar', name: 'العربية' }, { code: 'es', name: 'Español' },
  { code: 'fr', name: 'Français' }, { code: 'ru', name: 'Русский' },
  { code: 'tr', name: 'Türkçe' },
]

export default function LabelSimplificationPanel({
  sig, drugName, language = 'en', patientAge, onUseInstructions,
}: Props) {
  const [lang, setLang]           = useState(language)
  const [instructions, setInstr]  = useState('')
  const [env, setEnv]             = useState<Envelope | null>(null)
  const [showAddRef, setShowAddRef] = useState(false)
  const [refText, setRefText]     = useState('')

  const simplifyMutation = useMutation({
    mutationFn: (l: string) => apiClient.post('/intelligence/label/simplify', {
      sig, drug_name: drugName, language: l, patient_age: patientAge,
    }).then(r => r.data as Envelope),
    onSuccess: (e) => { setEnv(e); setInstr(e.result.patient_instructions) },
  })

  const addRefMutation = useMutation({
    mutationFn: () => apiClient.post('/intelligence/label/add-reference', {
      drug_name: drugName, dosing_text: refText, source: 'pharmacist',
    }).then(r => r.data),
    onSuccess: () => { setRefText(''); setShowAddRef(false); simplifyMutation.mutate(lang) },
  })

  // Auto-run on mount / when sig|drug changes.
  useEffect(() => {
    if (sig && drugName) simplifyMutation.mutate(lang)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sig, drugName])

  const ref = env?.result.reference_dosing ?? []

  return (
    <div className="bg-white border border-gray-200 rounded-xl p-3">
      <div className="flex items-center justify-between mb-2">
        <div className="flex items-center gap-2">
          <span className="text-base">🗣</span>
          <h3 className="text-xs font-semibold text-gray-800">Plain-Language Instructions</h3>
          {env && <TierBadge tier={env.tier_used} degraded={env.degraded} optionsOffline={env.options_offline} compact />}
        </div>
        <select
          value={lang}
          onChange={e => { setLang(e.target.value); simplifyMutation.mutate(e.target.value) }}
          className="text-[11px] border border-gray-200 rounded px-1.5 py-0.5">
          {LANGUAGES.map(l => <option key={l.code} value={l.code}>{l.name}</option>)}
        </select>
      </div>

      {/* Patient-facing instructions (default for label) */}
      {simplifyMutation.isPending ? (
        <p className="text-xs text-gray-400 py-3 text-center animate-pulse">Simplifying…</p>
      ) : (
        <>
          <textarea
            value={instructions}
            onChange={e => setInstr(e.target.value)}
            rows={2}
            dir={lang === 'fa' || lang === 'ar' ? 'rtl' : 'ltr'}
            className="w-full border border-gray-200 rounded-lg px-2.5 py-1.5 text-sm focus:outline-none focus:ring-2 focus:ring-purple-200"
          />
          {onUseInstructions && (
            <button
              onClick={() => onUseInstructions(instructions)}
              className="mt-1 text-[11px] px-2 py-1 bg-purple-600 text-white rounded hover:bg-purple-700">
              ✓ Use as label directions
            </button>
          )}
          {env?.result.expanded_sig && (
            <p className="text-[10px] text-gray-400 mt-1">
              from SIG: <span className="font-mono">{sig}</span> → {env.result.expanded_sig}
            </p>
          )}
        </>
      )}

      {/* Pharmacist reference dosing */}
      <div className="mt-3 border-t border-gray-100 pt-2">
        <div className="flex items-center justify-between mb-1">
          <h4 className="text-[11px] font-semibold text-indigo-700 uppercase tracking-wide">
            📚 Reference Dosing <span className="text-gray-400 font-normal normal-case">(pharmacist)</span>
          </h4>
          <button onClick={() => setShowAddRef(s => !s)}
            className="text-[10px] text-indigo-600 hover:underline">
            {showAddRef ? 'cancel' : '+ add reference'}
          </button>
        </div>

        {showAddRef && (
          <div className="mb-2 space-y-1">
            <textarea
              value={refText}
              onChange={e => setRefText(e.target.value)}
              rows={2}
              placeholder={`Paste a dosing reference for ${drugName} (textbook table, package insert, formulary)…`}
              className="w-full border border-indigo-200 rounded px-2 py-1 text-xs"
            />
            <button
              onClick={() => addRefMutation.mutate()}
              disabled={addRefMutation.isPending || refText.trim().length < 10}
              className="text-[11px] px-2 py-1 bg-indigo-600 text-white rounded hover:bg-indigo-700 disabled:opacity-40">
              {addRefMutation.isPending ? 'Saving…' : 'Save to corpus'}
            </button>
          </div>
        )}

        {ref.length === 0 ? (
          <p className="text-[11px] text-gray-400">
            No reference dosing on file for {drugName}. Add one to train the corpus —
            it becomes available even offline.
          </p>
        ) : (
          <div className="space-y-1.5">
            {ref.map((r, i) => (
              <div key={i} className="bg-indigo-50 border border-indigo-100 rounded px-2.5 py-1.5">
                <p className="text-xs text-gray-700">{r.text}</p>
                <p className="text-[10px] text-indigo-400 mt-0.5">
                  source: {r.source} · match {Math.round(r.score * 100)}%
                </p>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  )
}
