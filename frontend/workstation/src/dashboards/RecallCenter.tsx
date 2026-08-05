/**
 * RecallCenter — «فراخوان دارو»
 *
 * A recall is a stopwatch, so the screen is ordered by urgency rather than by
 * data structure: how many units are still sellable, one button to take them
 * off the shelf, then who received the rest.
 *
 * The design decision that matters is the warning. `RecallAlertBanner` tells
 * you a recall exists; this tells you what you can and — crucially — cannot
 * account for. Against this pharmacy's current data the trace is 0%, because
 * every fill predating the lot link carries no lot at all. A patient list read
 * without that context invites exactly the wrong conclusion from a short list,
 * so the warning is rendered as a block the eye cannot skip, immediately above
 * the names, and never as a footnote.
 *
 * Quarantine is the only destructive-looking action offered without ceremony,
 * and deliberately so: taking stock out of use must never wait for a signature.
 * Destroying it is a separate approved write-off, and closing a recall over
 * outstanding blockers demands a reason that stays on the record.
 */
import { useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { inventoryRecallApi, apiErrorText } from '../lib/api'

interface RecallCase {
  id: string; reference: string; severity: string; scope: string; status: string
  reason: string; source: string | null
  units_on_shelf: number; units_blocked: number; units_dispensed: number
  lots_affected: number; patients_identified: number; patients_notified: number
  traceable_pct: number | null; trace_complete: boolean
  opened_at: string | null; closed_at: string | null
  closed_over_blockers: boolean; closure_reason: string | null
  impact: { warning?: string | null; urgent_action?: string | null } | null
}
interface Line {
  id: string; state: string; quantity: number; lot_number: string | null
  location: string | null; lot_id: string | null; fill_id: string | null
  patient_id: string | null; patient_name: string | null
  dispensed_at: string | null; action_required: string | null
  action_taken: string | null; note: string | null
}
interface CaseDetail extends RecallCase {
  lines: Line[]
  outstanding: { lots_to_quarantine: number; patients_to_notify: number }
}
interface PatientList {
  patients: { patient_id: string | null; name: string | null; phone: string | null
              units: number; first_dispensed: string | null
              last_dispensed: string | null; notified: boolean }[]
  count: number; traceable_pct: number | null; trace_complete: boolean
  warning: string | null
}

const fa = (n: number | null | undefined) =>
  n == null ? '—' : new Intl.NumberFormat('fa-IR').format(n)
const faDT = (s: string | null) => s ? new Date(s).toLocaleString('fa-IR') : '—'

const SEVERITY: Record<string, { label: string; cls: string }> = {
  I:   { label: 'کلاس I — خطر جدی', cls: 'bg-rose-500/15 text-rose-300 border-rose-500/50' },
  II:  { label: 'کلاس II — خطر گذرا', cls: 'bg-amber-500/15 text-amber-300 border-amber-500/40' },
  III: { label: 'کلاس III — کم‌خطر', cls: 'bg-sky-500/15 text-sky-300 border-sky-500/40' },
}
const STATUS_FA: Record<string, string> = {
  open: 'باز', contained: 'مهارشده', closed: 'بسته‌شده', cancelled: 'لغوشده',
}
const STATE_FA: Record<string, string> = {
  on_shelf: 'روی قفسه', blocked: 'قرنطینه', dispensed: 'تحویل‌شده',
  already_gone: 'قبلاً خارج شده', untraceable: 'غیرقابل ردیابی',
}
const SCOPES: [string, string][] = [
  ['lot_number', 'شماره بچ'], ['irc', 'کد IRC'],
  ['gtin', 'بارکد GTIN'], ['supplier_batch', 'بچ تأمین‌کننده'],
]

export default function RecallCenter() {
  const qc = useQueryClient()
  const [openId, setOpenId] = useState<string | null>(null)
  const [showForm, setShowForm] = useState(false)
  const [msg, setMsg] = useState<{ kind: 'ok' | 'err' | 'warn'; text: string } | null>(null)
  const [busy, setBusy] = useState(false)

  const { data, isLoading, error } = useQuery<{ recalls: RecallCase[]; count: number }>({
    queryKey: ['recalls'],
    queryFn: () => inventoryRecallApi.list().then(r => r.data),
    refetchInterval: 30_000,
  })
  const { data: detail } = useQuery<CaseDetail>({
    queryKey: ['recall', openId],
    queryFn: () => inventoryRecallApi.get(openId!).then(r => r.data),
    enabled: !!openId,
  })
  const { data: patients } = useQuery<PatientList>({
    queryKey: ['recall-patients', openId],
    queryFn: () => inventoryRecallApi.patients(openId!).then(r => r.data),
    enabled: !!openId,
  })

  const refresh = () => {
    qc.invalidateQueries({ queryKey: ['recalls'] })
    qc.invalidateQueries({ queryKey: ['recall'] })
    qc.invalidateQueries({ queryKey: ['recall-patients'] })
    qc.invalidateQueries({ queryKey: ['inv-admin-items'] })
  }

  const quarantine = async (id: string, units: number) => {
    if (!window.confirm(
      `${fa(units)} واحد از قفسه برداشته و قرنطینه می‌شود.\n`
      + 'این کار فوری است و به تأیید نیاز ندارد. امحا مرحلهٔ جداگانه‌ای است.')) return
    setBusy(true); setMsg(null)
    try {
      const { data: r } = await inventoryRecallApi.quarantine(id)
      setMsg({ kind: 'ok', text: `${fa(r.lots_quarantined)} بچ قرنطینه شد. ${r.note}` })
      refresh()
    } catch (e) { setMsg({ kind: 'err', text: apiErrorText(e) }) }
    finally { setBusy(false) }
  }

  const markLine = async (id: string, lineId: string, action: string) => {
    setBusy(true); setMsg(null)
    try {
      const { data: r } = await inventoryRecallApi.lineAction(id, lineId, { action })
      setMsg({ kind: 'ok', text: `ثبت شد — ${fa(r.patients_notified)} بیمار مطلع شده‌اند.` })
      refresh()
    } catch (e) { setMsg({ kind: 'err', text: apiErrorText(e) }) }
    finally { setBusy(false) }
  }

  const close = async (id: string) => {
    setBusy(true); setMsg(null)
    try {
      const { data: r } = await inventoryRecallApi.close(id, {})
      if (r.closed) { setMsg({ kind: 'ok', text: 'فراخوان بسته شد.' }); refresh(); return }
      const forced = window.confirm(
        'این فراخوان هنوز قابل بستن نیست:\n\n• ' + r.blockers.join('\n• ')
        + '\n\nبستن با وجود این موارد؟ دلیل ثبت و نگهداری می‌شود.')
      if (!forced) { setMsg({ kind: 'warn', text: 'باز ماند: ' + r.blockers.join(' · ') }); return }
      const reason = window.prompt('دلیل بستن با وجود موارد باز؟')
      if (!reason || reason.trim().length < 3) {
        setMsg({ kind: 'err', text: 'دلیل لازم است.' }); return
      }
      const { data: f } = await inventoryRecallApi.close(id, { force_reason: reason })
      setMsg({ kind: 'warn', text: f.closed
        ? 'با وجود موارد باز بسته شد؛ موارد در سابقه ثبت ماند.' : 'بسته نشد.' })
      refresh()
    } catch (e) { setMsg({ kind: 'err', text: apiErrorText(e) }) }
    finally { setBusy(false) }
  }

  const active = (data?.recalls ?? []).filter(r => r.status === 'open' || r.status === 'contained')
  const urgentUnits = active.reduce((n, r) => n + r.units_on_shelf, 0)

  return (
    <div className="p-4 space-y-4 text-slate-100" dir="rtl">
      <div className="flex flex-wrap items-center gap-3">
        <h2 className="text-lg font-bold">فراخوان دارو</h2>
        <span className="text-[11px] text-slate-500">
          پاسخ داروخانه به فراخوان — چه داریم، کجاست، به چه کسی داده‌ایم
        </span>
        <button onClick={() => setShowForm(v => !v)}
          className="mr-auto px-3 py-1.5 bg-rose-600 hover:bg-rose-500 rounded-lg text-sm">
          + فراخوان جدید
        </button>
      </div>

      {urgentUnits > 0 && (
        <div className="bg-rose-500/10 border border-rose-500/50 rounded-lg px-4 py-3">
          <p className="text-rose-200 font-semibold text-sm">
            ⚠ {fa(urgentUnits)} واحد از اقلام فراخوان‌شده هنوز قابل عرضه است
          </p>
          <p className="text-[11px] text-rose-300/80 pt-0.5">
            پیش از هر کار دیگری قرنطینه کنید — تا آن زمان ممکن است تحویل بیمار شود.
          </p>
        </div>)}

      {msg && <p className={`text-sm ${msg.kind === 'ok' ? 'text-emerald-400'
        : msg.kind === 'warn' ? 'text-amber-300' : 'text-red-400'}`}>{msg.text}</p>}
      {error && <p className="text-sm text-red-400">{apiErrorText(error)}</p>}
      {isLoading && <p className="text-sm text-slate-400">در حال بارگذاری…</p>}

      {showForm && <OpenForm onDone={(m) => { setMsg(m); setShowForm(false); refresh() }} />}

      {data?.count === 0 && !showForm && (
        <p className="text-[12px] text-slate-500 py-4">هیچ فراخوانی ثبت نشده است.</p>)}

      {/* ── cases ─────────────────────────────────────────────────────── */}
      <div className="space-y-2">
        {data?.recalls.map(r => {
          const sev = SEVERITY[r.severity] ?? SEVERITY.II
          const isOpen = openId === r.id
          const done = r.status === 'closed' || r.status === 'cancelled'
          return (
            <div key={r.id} className={`border rounded-lg ${
              done ? 'border-slate-800 bg-slate-900/40'
                   : r.units_on_shelf > 0 ? 'border-rose-500/50 bg-slate-800/60'
                                          : 'border-slate-700 bg-slate-800/50'}`}>
              <button onClick={() => setOpenId(isOpen ? null : r.id)}
                      className="w-full flex flex-wrap items-center gap-3 px-4 py-2.5 text-right">
                <span className={`text-[10px] px-1.5 py-0.5 rounded border ${sev.cls}`}>
                  {sev.label}
                </span>
                <span className="font-mono text-sm">{r.reference}</span>
                <span className="text-[11px] text-slate-400">{r.scope}</span>
                <span className={`text-[10px] px-1.5 py-0.5 rounded ${
                  done ? 'bg-slate-700 text-slate-400' : 'bg-indigo-600/25 text-indigo-200'}`}>
                  {STATUS_FA[r.status] || r.status}
                </span>
                {r.units_on_shelf > 0 && (
                  <span className="text-[11px] text-rose-300 font-semibold">
                    {fa(r.units_on_shelf)} واحد روی قفسه
                  </span>)}
                {!r.trace_complete && (
                  <span className="text-[11px] text-amber-300"
                        title="بخشی از تحویل‌ها به بچ متصل نیست">
                    ردیابی {fa(r.traceable_pct ?? 0)}٪
                  </span>)}
                <span className="text-[11px] text-slate-500">
                  {fa(r.patients_notified)}/{fa(r.patients_identified)} بیمار مطلع
                </span>
                <span className="mr-auto text-slate-500 text-xs">{isOpen ? '▲' : '▼'}</span>
              </button>

              {isOpen && detail && detail.id === r.id && (
                <div className="px-4 pb-4 pt-2 border-t border-slate-700/60 space-y-4">
                  <p className="text-[12px] text-slate-400">
                    {detail.reason}
                    {detail.source && <span className="text-slate-500"> · منبع: {detail.source}</span>}
                    <span className="text-slate-500"> · باز شده {faDT(detail.opened_at)}</span>
                  </p>

                  <div className="flex flex-wrap gap-x-6 gap-y-1 text-[12px]">
                    <Stat label="روی قفسه" value={fa(detail.units_on_shelf)}
                          tone={detail.units_on_shelf > 0 ? 'bad' : 'ok'} />
                    <Stat label="قرنطینه" value={fa(detail.units_blocked)} />
                    <Stat label="تحویل‌شده" value={fa(detail.units_dispensed)} />
                    <Stat label="بچ‌های درگیر" value={fa(detail.lots_affected)} />
                  </div>

                  {/* the action that comes before everything else */}
                  {detail.units_on_shelf > 0 && (
                    <button onClick={() => quarantine(detail.id, detail.units_on_shelf)}
                            disabled={busy}
                            className="w-full py-2.5 bg-rose-600 hover:bg-rose-500 rounded-lg
                                       font-semibold disabled:opacity-50">
                      قرنطینهٔ فوری همهٔ {fa(detail.outstanding.lots_to_quarantine)} بچ
                    </button>)}

                  {/* the blind spot — a block, never a footnote */}
                  {!detail.trace_complete && (
                    <div className="bg-amber-500/10 border-2 border-amber-500/50 rounded-lg p-3">
                      <p className="text-amber-200 font-semibold text-[12px]">
                        ⚠ فهرست بیماران کامل نیست — ردیابی {fa(detail.traceable_pct ?? 0)}٪
                      </p>
                      <p className="text-[11px] text-amber-300/90 pt-1 leading-relaxed">
                        {detail.impact?.warning}
                      </p>
                    </div>)}

                  {/* patients */}
                  <div>
                    <p className="text-[12px] font-semibold pb-1">
                      بیماران شناسایی‌شده ({fa(patients?.count ?? 0)})
                    </p>
                    {patients?.count === 0
                      ? <p className="text-[11px] text-slate-500">
                          هیچ تحویلی از این بچ به بیمار مشخصی متصل نیست.
                          {!detail.trace_complete && ' این به معنای نبودِ بیمار متأثر نیست — هشدار بالا را بخوانید.'}
                        </p>
                      : (
                      <div className="overflow-x-auto border border-slate-700/60 rounded">
                        <table className="w-full text-[11px]">
                          <thead className="text-slate-500">
                            <tr>{['بیمار', 'تلفن', 'مقدار', 'آخرین تحویل', 'وضعیت', ''].map(h =>
                              <th key={h} className="text-right px-2 py-1 font-normal">{h}</th>)}</tr>
                          </thead>
                          <tbody>
                            {patients?.patients.map((p, i) => {
                              const line = detail.lines.find(
                                l => l.patient_id === p.patient_id && l.state === 'dispensed')
                              return (
                                <tr key={i} className="border-t border-slate-800">
                                  <td className="px-2 py-1">{p.name || '—'}</td>
                                  <td className="px-2 py-1 font-mono">{p.phone || '—'}</td>
                                  <td className="px-2 py-1 font-mono tabular-nums">{fa(p.units)}</td>
                                  <td className="px-2 py-1 whitespace-nowrap text-slate-400">
                                    {faDT(p.last_dispensed)}
                                  </td>
                                  <td className="px-2 py-1">
                                    {p.notified
                                      ? <span className="text-emerald-400">مطلع شد</span>
                                      : <span className="text-amber-300">در انتظار تماس</span>}
                                  </td>
                                  <td className="px-2 py-1">
                                    {!p.notified && line && (
                                      <button onClick={() => markLine(detail.id, line.id, 'notified')}
                                              disabled={busy}
                                              className="text-indigo-300 hover:underline disabled:opacity-50">
                                        ثبت اطلاع‌رسانی
                                      </button>)}
                                  </td>
                                </tr>)
                            })}
                          </tbody>
                        </table>
                      </div>)}
                  </div>

                  {/* affected stock lines */}
                  {detail.lines.some(l => l.state !== 'dispensed') && (
                    <div>
                      <p className="text-[12px] font-semibold pb-1">موجودی درگیر</p>
                      <div className="overflow-x-auto border border-slate-700/60 rounded">
                        <table className="w-full text-[11px]">
                          <thead className="text-slate-500">
                            <tr>{['بچ', 'محل', 'مقدار', 'وضعیت', 'اقدام لازم', 'انجام‌شده'].map(h =>
                              <th key={h} className="text-right px-2 py-1 font-normal">{h}</th>)}</tr>
                          </thead>
                          <tbody>
                            {detail.lines.filter(l => l.state !== 'dispensed').map(l => (
                              <tr key={l.id} className="border-t border-slate-800">
                                <td className="px-2 py-1 font-mono">{l.lot_number || '—'}</td>
                                <td className="px-2 py-1">{l.location || '—'}</td>
                                <td className="px-2 py-1 font-mono tabular-nums">{fa(l.quantity)}</td>
                                <td className="px-2 py-1">
                                  <span className={l.state === 'on_shelf'
                                    ? 'text-rose-300' : 'text-slate-400'}>
                                    {STATE_FA[l.state] || l.state}
                                  </span>
                                </td>
                                <td className="px-2 py-1 text-slate-500 max-w-xs truncate"
                                    title={l.action_required || ''}>{l.action_required}</td>
                                <td className="px-2 py-1">
                                  {l.action_taken
                                    ? <span className="text-emerald-400">{l.action_taken}</span>
                                    : <span className="text-slate-600">—</span>}
                                </td>
                              </tr>))}
                          </tbody>
                        </table>
                      </div>
                    </div>)}

                  {!done && (
                    <div className="flex flex-wrap gap-2 items-center text-[12px]">
                      <button onClick={() => close(detail.id)} disabled={busy}
                        className="px-3 py-1.5 bg-slate-700 hover:bg-slate-600 rounded disabled:opacity-50">
                        بستن فراخوان
                      </button>
                      <span className="text-slate-500">
                        باقی‌مانده: {fa(detail.outstanding.lots_to_quarantine)} بچ برای قرنطینه ·
                        {' '}{fa(detail.outstanding.patients_to_notify)} بیمار برای تماس
                      </span>
                    </div>)}

                  {done && detail.closed_over_blockers && (
                    <p className="text-[11px] text-amber-300">
                      با وجود موارد باز بسته شد — دلیل: {detail.closure_reason}
                    </p>)}
                </div>)}
            </div>)
        })}
      </div>
    </div>
  )
}

function Stat({ label, value, tone }: { label: string; value: string
                                        tone?: 'ok' | 'bad' }) {
  return (
    <div>
      <div className="text-[10px] text-slate-500">{label}</div>
      <div className={`font-bold tabular-nums ${
        tone === 'bad' ? 'text-rose-300' : tone === 'ok' ? 'text-emerald-400' : ''}`}>
        {value}
      </div>
    </div>
  )
}

function OpenForm({ onDone }: {
  onDone: (m: { kind: 'ok' | 'err' | 'warn'; text: string }) => void }) {
  const [f, setF] = useState({ reference: '', scope_type: 'lot_number',
                               scope_value: '', severity: 'II', reason: '', source: '' })
  const [busy, setBusy] = useState(false)
  const set = (k: string) => (e: React.ChangeEvent<HTMLInputElement | HTMLSelectElement>) =>
    setF(v => ({ ...v, [k]: e.target.value }))

  const submit = async () => {
    setBusy(true)
    try {
      const { data } = await inventoryRecallApi.open({
        reference: f.reference.trim(), scope_type: f.scope_type,
        scope_value: f.scope_value.trim(), severity: f.severity,
        reason: f.reason.trim(), source: f.source.trim() || undefined,
      })
      onDone({
        kind: data.on_shelf > 0 ? 'warn' : 'ok',
        text: data.on_shelf > 0
          ? `فراخوان باز شد — ${fa(data.on_shelf)} واحد هنوز روی قفسه است. فوراً قرنطینه کنید.`
          : `فراخوان باز شد — چیزی روی قفسه نیست. ${data.next_step}`,
      })
    } catch (e) { onDone({ kind: 'err', text: apiErrorText(e) }) }
    finally { setBusy(false) }
  }

  return (
    <div className="bg-slate-800/60 border border-rose-700/50 rounded-lg p-4 space-y-3">
      <p className="text-sm font-semibold">ثبت فراخوان جدید</p>
      <p className="text-[11px] text-slate-500">
        با ثبت، موجودی درگیر بلافاصله شناسایی می‌شود — نخستین عدد لازم، مقدارِ هنوز
        قابل عرضه است.
      </p>
      <div className="flex flex-wrap gap-2 text-[12px]">
        <Field label="شناسهٔ فراخوان">
          <input value={f.reference} onChange={set('reference')} placeholder="REC-1405-01"
            className="w-40 bg-slate-900 border border-slate-600 rounded px-2 py-1" />
        </Field>
        <Field label="دامنه">
          <select value={f.scope_type} onChange={set('scope_type')}
            className="w-36 bg-slate-900 border border-slate-600 rounded px-2 py-1">
            {SCOPES.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
          </select>
        </Field>
        <Field label="مقدار دامنه">
          <input value={f.scope_value} onChange={set('scope_value')}
            className="w-44 bg-slate-900 border border-slate-600 rounded px-2 py-1" />
        </Field>
        <Field label="شدت">
          <select value={f.severity} onChange={set('severity')}
            className="w-48 bg-slate-900 border border-slate-600 rounded px-2 py-1">
            {Object.entries(SEVERITY).map(([v, s]) =>
              <option key={v} value={v}>{s.label}</option>)}
          </select>
        </Field>
        <Field label="منبع">
          <input value={f.source} onChange={set('source')} placeholder="سازنده / سازمان"
            className="w-36 bg-slate-900 border border-slate-600 rounded px-2 py-1" />
        </Field>
      </div>
      <Field label="دلیل فراخوان">
        <input value={f.reason} onChange={set('reason')}
          className="w-full bg-slate-900 border border-slate-600 rounded px-2 py-1 text-[12px]" />
      </Field>
      <button onClick={submit}
        disabled={busy || !f.reference || !f.scope_value || f.reason.trim().length < 3}
        className="px-4 py-1.5 bg-rose-600 hover:bg-rose-500 rounded-lg text-sm disabled:opacity-40">
        ثبت و شناسایی موجودی درگیر
      </button>
    </div>
  )
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <label className="flex flex-col gap-1">
      <span className="text-slate-500 text-[10px]">{label}</span>
      {children}
    </label>
  )
}
