import { useState } from 'react'
import { clinicalApi } from '../lib/api'
import { useLang } from '../lib/i18n'

interface Finding { rule_id?: string; participants: { name: string }[]; mechanism: string; severity: string }

export default function PhysicianLetterModal({
  patientId, rxId, findings, onClose,
}: { patientId: string; rxId?: string; findings: Finding[]; onClose: () => void }) {
  const { t } = useLang()
  const [language, setLanguage] = useState('fa')
  const [physician, setPhysician] = useState('')
  const [councilId, setCouncilId] = useState('')
  const [letter, setLetter] = useState<string | null>(null)
  const [letterHtml, setLetterHtml] = useState<string | null>(null)
  const [letterId, setLetterId] = useState<string | null>(null)
  const [editing, setEditing] = useState(false)
  const [draft, setDraft] = useState('')
  const [saved, setSaved] = useState(false)
  const [loading, setLoading] = useState(false)

  const generate = async () => {
    if (!physician.trim() || !councilId.trim()) return
    setLoading(true)
    try {
      const { data } = await clinicalApi.generatePhysicianLetter({
        patient_id: patientId, rx_id: rxId, language,
        physician_name: physician, council_id: councilId, findings,
      })
      setLetter(data.letter_text)
      setLetterHtml(data.letter_html)
      setLetterId(data.id ?? null)
      setEditing(false); setSaved(false)
    } catch { setLetter('Could not generate letter — try again.'); setLetterHtml(null) }
    finally { setLoading(false) }
  }

  const saveEdit = async () => {
    if (!letterId || !draft.trim()) return
    setLoading(true)
    try {
      const { data } = await clinicalApi.revisePhysicianLetter(letterId, draft)
      setLetter(data.letter_text)
      setLetterHtml(data.letter_html)
      setEditing(false); setSaved(true)
    } catch { /* keep editing */ }
    finally { setLoading(false) }
  }

  const print = () => {
    if (!letterHtml) return
    const w = window.open('', '_blank')
    if (!w) return
    w.document.write(letterHtml)   // server-rendered RTL/A4 HTML (single source of truth)
    w.document.close(); w.focus(); w.print()
  }

  return (
    <div className="fixed inset-0 z-50 bg-black/40 flex items-center justify-center p-4" onClick={onClose}>
      <div className="cd-card bg-surface max-w-2xl w-full p-4 space-y-3" onClick={e => e.stopPropagation()}>
        <div className="flex items-center">
          <h3 className="cd-ui text-sm font-bold text-ink">Physician responsibility letter</h3>
          <button onClick={onClose} className="ml-auto text-ink3 text-sm">✕</button>
        </div>
        <div className="flex gap-2">
          <select value={language} onChange={e => setLanguage(e.target.value)}
            className="cd-ui text-sm bg-surface2 border border-line2 rounded-lg px-2 py-2 text-ink">
            <option value="fa">فارسی</option><option value="en">English</option>
          </select>
          <input value={physician} onChange={e => setPhysician(e.target.value)} placeholder="Physician name"
            className="cd-ui flex-1 text-sm bg-surface2 border border-line2 rounded-lg px-3 py-2 text-ink" />
          <input value={councilId} onChange={e => setCouncilId(e.target.value)} placeholder="Council ID"
            className="cd-ui w-32 text-sm bg-surface2 border border-line2 rounded-lg px-3 py-2 text-ink" />
        </div>
        <div className="flex gap-2 items-center">
          <button onClick={generate} disabled={loading || !physician.trim() || !councilId.trim()}
            className="cd-ui px-4 py-2 bg-intel text-white text-sm rounded-lg disabled:opacity-50">
            {loading ? '…' : letter ? 'Regenerate' : 'Generate'}</button>
          {letter && !editing && (
            <button onClick={() => { setDraft(letter); setEditing(true); setSaved(false) }}
              className="cd-ui px-4 py-2 bg-surface2 border border-line2 text-ink2 text-sm rounded-lg">✎ Edit</button>
          )}
          {letter && !editing && letterHtml &&
            <button onClick={print} className="cd-ui px-4 py-2 bg-[#34d399] text-white text-sm rounded-lg">Print</button>}
          {saved && !editing && <span className="cd-ui text-xs text-safe">✓ Saved to record</span>}
        </div>

        {letter && !editing && (
          <div dir={language === 'en' ? 'ltr' : 'rtl'}
            className="cd-narr text-sm text-ink bg-surface2 border border-line rounded-lg p-3 max-h-80 overflow-y-auto whitespace-pre-wrap leading-[1.9]">
            {letter}
          </div>
        )}

        {editing && (
          <div className="space-y-2">
            <p className="cd-ui text-[11px] text-ink3">
              {t('Correct any wrong surname or honorific. Saving overwrites the recorded legal version (the edit is audited).',
                 'نام خانوادگی یا عنوان نادرست را اصلاح کنید. ذخیره، نسخهٔ حقوقی ثبت‌شده را بازنویسی می‌کند (ویرایش ممیزی می‌شود).')}
            </p>
            <textarea value={draft} onChange={e => setDraft(e.target.value)} rows={12}
              dir={language === 'en' ? 'ltr' : 'rtl'}
              className="cd-narr w-full text-sm text-ink bg-surface2 border border-line2 rounded-lg p-3 leading-[1.9] focus:outline-none focus:ring-2 focus:ring-intel/30" />
            <div className="flex gap-2">
              <button onClick={saveEdit} disabled={loading || !draft.trim()}
                className="cd-ui px-4 py-2 bg-intel text-white text-sm rounded-lg disabled:opacity-50">
                {loading ? '…' : 'Save edits'}</button>
              <button onClick={() => setEditing(false)}
                className="cd-ui px-4 py-2 bg-surface2 border border-line2 text-ink2 text-sm rounded-lg">Cancel</button>
            </div>
          </div>
        )}
      </div>
    </div>
  )
}
