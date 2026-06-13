/**
 * SOAPDraftPanel — #18 Automated Clinical Documentation
 * ======================================================
 * Generates an editable SOAP note draft from a consultation transcript, then
 * lets the pharmacist review, edit, and confirm-to-save (pharmacist is the signer).
 *
 * Pass either a transcriptId (server pulls the diarized transcript) or raw
 * transcriptText. Consumes the §1.2 envelope; shows a TierBadge.
 */
import { useState } from 'react'
import { useMutation } from '@tanstack/react-query'
import { apiClient } from '../lib/api'
import TierBadge from './TierBadge'

interface SOAP { subjective: string; objective: string; assessment: string; plan: string }
interface SOAPResult { soap: SOAP; is_draft: boolean; draft_label?: string; message?: string }
interface Envelope {
  result: SOAPResult; tier_used: 'local' | 'cloud' | 'hybrid'
  degraded: boolean; confidence: number; options_offline: string[]
}

interface Props {
  transcriptId?:   string
  transcriptText?: string
  patientContext?: string
  onSave?: (soap: SOAP) => void
}

const SECTIONS: { key: keyof SOAP; label: string; hint: string }[] = [
  { key: 'subjective', label: 'S — Subjective', hint: 'What the patient reported' },
  { key: 'objective',  label: 'O — Objective',  hint: 'Meds, labs, observations' },
  { key: 'assessment', label: 'A — Assessment', hint: 'Clinical interpretation' },
  { key: 'plan',       label: 'P — Plan',       hint: 'Actions / follow-up' },
]

export default function SOAPDraftPanel({ transcriptId, transcriptText, patientContext, onSave }: Props) {
  const [soap, setSoap]       = useState<SOAP | null>(null)
  const [env, setEnv]         = useState<Envelope | null>(null)
  const [saved, setSaved]     = useState(false)

  const draftMutation = useMutation({
    mutationFn: () => apiClient.post('/intelligence/docs/draft-soap', {
      transcript_id: transcriptId, transcript_text: transcriptText, patient_context: patientContext,
    }).then(r => r.data as Envelope),
    onSuccess: (e) => { setEnv(e); setSoap(e.result.soap); setSaved(false) },
  })

  const setField = (k: keyof SOAP, v: string) =>
    setSoap(s => (s ? { ...s, [k]: v } : s))

  return (
    <div className="bg-white border border-gray-200 rounded-xl p-4">
      <div className="flex items-center justify-between mb-3">
        <div className="flex items-center gap-2">
          <span className="text-lg">📝</span>
          <h3 className="text-sm font-semibold text-gray-800">Clinical Note (SOAP)</h3>
          {env && <TierBadge tier={env.tier_used} degraded={env.degraded} optionsOffline={env.options_offline} compact />}
        </div>
        {!soap && (
          <button
            onClick={() => draftMutation.mutate()}
            disabled={draftMutation.isPending}
            className="text-xs px-3 py-1.5 bg-purple-600 text-white rounded-lg hover:bg-purple-700 disabled:opacity-40">
            {draftMutation.isPending ? '⌛ Drafting…' : '✨ Generate draft'}
          </button>
        )}
      </div>

      {!soap && !draftMutation.isPending && (
        <p className="text-xs text-gray-400 text-center py-6">
          Generate a SOAP draft from the consultation transcript. You review and sign.
        </p>
      )}

      {draftMutation.isPending && (
        <div className="text-center py-6">
          <div className="text-2xl animate-pulse">🔍</div>
          <p className="text-xs text-gray-500 mt-1">Extracting SOAP from transcript…</p>
        </div>
      )}

      {soap && (
        <div className="space-y-3">
          {env?.result.draft_label && (
            <div className="text-[11px] text-amber-700 bg-amber-50 border border-amber-200 rounded px-2 py-1">
              ⚠ {env.result.draft_label}
            </div>
          )}
          {SECTIONS.map(sec => (
            <div key={sec.key}>
              <label className="flex items-center gap-2 text-[11px] font-semibold text-gray-600 mb-1">
                {sec.label}
                <span className="text-gray-400 font-normal">· {sec.hint}</span>
              </label>
              <textarea
                value={soap[sec.key]}
                onChange={e => setField(sec.key, e.target.value)}
                rows={sec.key === 'subjective' || sec.key === 'plan' ? 3 : 2}
                className="w-full border border-gray-200 rounded-lg px-2.5 py-1.5 text-sm resize-y focus:outline-none focus:ring-2 focus:ring-purple-200"
              />
            </div>
          ))}
          <div className="flex gap-2">
            <button
              onClick={() => { onSave?.(soap); setSaved(true) }}
              className="flex-1 py-2 bg-green-600 text-white text-sm rounded-lg hover:bg-green-700 font-medium">
              ✅ Confirm & Save Note
            </button>
            <button
              onClick={() => draftMutation.mutate()}
              className="px-3 py-2 border border-gray-200 text-gray-600 text-sm rounded-lg hover:bg-gray-50">
              🔄 Regenerate
            </button>
          </div>
          {saved && <p className="text-xs text-green-600 text-center">✓ Note saved to the patient record.</p>}
        </div>
      )}
    </div>
  )
}
