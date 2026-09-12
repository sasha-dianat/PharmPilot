/**
 * ExceptionRegister — «دفتر مغایرت‌ها»
 *
 * The board an operator actually works from, and the bridge the Control Center
 * was missing: findings arrive ranked by impact, expand in place to every
 * affected row, and carry their own corrective actions. Nobody has to read an
 * NDC off one panel and re-type it into another.
 *
 * Three things it refuses to be:
 *
 * A list of counts. Every row shows what it costs, whom it can hurt, how sure
 * we are and how long it has been open — and «چرا این رتبه؟» prints the
 * arithmetic, because a priority order nobody can interrogate is one nobody
 * will trust.
 *
 * A preview. The drawer holds all 46 affected rows, not the ten the
 * reconciliation endpoint returns.
 *
 * A place to silently dismiss things. Accepting or suppressing a finding needs
 * a reason and `inventory:approve`; the ruling and its author land in a history
 * that cannot be rewritten.
 */
import { useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { inventoryExceptionsApi, apiErrorText } from '../lib/api'
import { useLang } from '../lib/i18n'
import { checkTitle } from '../lib/serverLabels'

export interface ExceptionRow {
  id: string; fingerprint: string; check: string; severity: string
  title_fa: string; entity_type: string; entity_key: string; status: string
  score: number; score_breakdown: { explanation: string } | null
  financial_impact: number; confidence: number; is_controlled: boolean
  row_count: number; occurrences: number; age_days: number | null
  first_seen_at: string | null; last_seen_at: string | null
  assigned_to: string | null; assignee_name: string | null
  disposition: string | null; disposition_reason: string | null
  resolved_at: string | null; resolved_by: string | null
}
interface Detail extends ExceptionRow {
  detail: string; remediation: string
  affected_rows: Record<string, unknown>[]
  history: { event: string; from: string | null; to: string | null
             reason: string | null; actor: string; at: string | null }[]
}

type Pair = readonly [string, string]

const SEV: Record<string, { chip: string; dot: string; label: Pair }> = {
  critical: { chip: 'bg-rose-500/15 text-rose-300 border-rose-500/40', dot: 'bg-rose-400', label: ['Critical', 'بحرانی'] },
  high:     { chip: 'bg-amber-500/15 text-amber-300 border-amber-500/40', dot: 'bg-amber-400', label: ['High', 'مهم'] },
  medium:   { chip: 'bg-sky-500/15 text-sky-300 border-sky-500/40', dot: 'bg-sky-400', label: ['Medium', 'متوسط'] },
  info:     { chip: 'bg-slate-600/20 text-slate-300 border-slate-600', dot: 'bg-slate-400', label: ['Info', 'اطلاعی'] },
}
const STATUS: Record<string, Pair> = {
  open: ['Open', 'باز'], assigned: ['Assigned', 'واگذارشده'], accepted: ['Accepted', 'پذیرفته‌شده'],
  suppressed: ['Suppressed', 'خاموش‌شده'], resolved: ['Resolved', 'برطرف‌شده'],
}
const FILTERS: [string, Pair][] = [
  ['active', ['Active', 'فعال']], ['open', ['Open', 'باز']], ['assigned', ['Assigned', 'واگذارشده']],
  ['accepted', ['Accepted', 'پذیرفته‌شده']], ['suppressed', ['Suppressed', 'خاموش']],
  ['resolved', ['Resolved', 'برطرف‌شده']], ['all', ['All', 'همه']],
]

export default function ExceptionRegister() {
  const { t, tp, n: fa, lang, dateTime } = useLang()
  const qc = useQueryClient()
  const [status, setStatus] = useState('active')
  const [controlledOnly, setControlledOnly] = useState(false)
  const [openId, setOpenId] = useState<string | null>(null)
  const [msg, setMsg] = useState<{ kind: 'ok' | 'err' | 'warn'; text: string } | null>(null)
  const [busy, setBusy] = useState(false)

  const { data, isLoading, error } = useQuery<{
    exceptions: ExceptionRow[]; total: number
    by_status: Record<string, { count: number; critical: number }> }>({
    queryKey: ['exceptions', status, controlledOnly],
    queryFn: () => inventoryExceptionsApi.list({
      status, controlled_only: controlledOnly || undefined, limit: 100,
    }).then(r => r.data),
    refetchInterval: 60_000,
  })
  const { data: detail } = useQuery<Detail>({
    queryKey: ['exception', openId],
    queryFn: () => inventoryExceptionsApi.get(openId!).then(r => r.data),
    enabled: !!openId,
  })

  const refresh = () => {
    qc.invalidateQueries({ queryKey: ['exceptions'] })
    qc.invalidateQueries({ queryKey: ['exception'] })
    qc.invalidateQueries({ queryKey: ['inv-reconciliation'] })
  }

  const runNow = async () => {
    setBusy(true); setMsg(null)
    try {
      const { data: r } = await inventoryExceptionsApi.run()
      setMsg({ kind: 'ok', text: t(
        `Check complete — ${fa(r.opened)} new, ${fa(r.recurred)} recurred, ${fa(r.resolved)} resolved.`,
        `بررسی انجام شد — ${fa(r.opened)} مورد تازه، ${fa(r.recurred)} تکرار، ${fa(r.resolved)} برطرف‌شده.`) })
      refresh()
    } catch (e) { setMsg({ kind: 'err', text: apiErrorText(e) }) }
    finally { setBusy(false) }
  }

  const dispose = async (id: string, disposition: string) => {
    const label = STATUS[disposition] ? tp(STATUS[disposition]) : disposition
    const reason = window.prompt(t(
      `Reason for marking this exception “${label}”? (recorded permanently)`,
      `دلیل «${label}» کردن این مغایرت؟ (در سابقه ثبت می‌شود و قابل حذف نیست)`))
    if (!reason || reason.trim().length < 3) {
      if (reason !== null) setMsg({ kind: 'err', text: t('The reason must be at least 3 characters.',
                                                         'دلیل باید حداقل ۳ نویسه باشد.') })
      return
    }
    setBusy(true); setMsg(null)
    try {
      const { data: r } = await inventoryExceptionsApi.dispose(id, { disposition, reason })
      setMsg({ kind: 'warn', text: r.note })
      refresh()
    } catch (e) { setMsg({ kind: 'err', text: apiErrorText(e) }) }
    finally { setBusy(false) }
  }

  const simulate = async (id: string) => {
    setBusy(true); setMsg(null)
    try {
      const { data: r } = await inventoryExceptionsApi.simulate(id)
      const lines = (r.plan as Record<string, unknown>[]).map(p =>
        `${p.lot_number ?? '—'}: ${p.action}` +
        (p.quantity != null ? t(` ${fa(Number(p.quantity))} units → ${fa(Number(p.on_hand_after))}`,
                                ` ${fa(Number(p.quantity))} واحد → ${fa(Number(p.on_hand_after))}`) : '') +
        (p.why ? ` (${p.why})` : ''))
      setMsg({ kind: 'warn', text: lines.length
        ? t(`Simulation (nothing changed): ${lines.join(' · ')}`,
            `شبیه‌سازی (بدون تغییر): ${lines.join(' · ')}`)
        : r.note })
    } catch (e) { setMsg({ kind: 'err', text: apiErrorText(e) }) }
    finally { setBusy(false) }
  }

  const counts = data?.by_status ?? {}
  const activeCritical = Object.entries(counts)
    .filter(([s]) => s !== 'resolved')
    .reduce((n, [, v]) => n + v.critical, 0)

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center gap-3">
        <span className="font-semibold text-sm">{t('Exception register', 'دفتر مغایرت‌ها')}</span>
        {activeCritical > 0 && (
          <span className="text-[11px] px-2 py-0.5 rounded-full bg-rose-500/15 text-rose-300 border border-rose-500/40">
            {t(`${fa(activeCritical)} critical open`, `${fa(activeCritical)} مورد بحرانی باز`)}
          </span>)}
        <span className="text-[11px] text-slate-500">
          {t('Ranked by financial impact, patient safety and certainty — not by severity alone',
             'رتبه‌بندی بر پایهٔ اثر مالی، ایمنی بیمار و قطعیت — نه صرفاً شدت')}
        </span>
        <button onClick={runNow} disabled={busy}
          className="ms-auto px-3 py-1.5 bg-indigo-600 hover:bg-indigo-500 rounded-lg text-sm disabled:opacity-50">
          ↻ {t('Run the check again', 'بررسی دوباره')}
        </button>
      </div>
      {msg && <p className={`text-sm ${msg.kind === 'ok' ? 'text-emerald-400'
        : msg.kind === 'warn' ? 'text-amber-300' : 'text-red-400'}`}>{msg.text}</p>}
      {error && <p className="text-sm text-red-400">{apiErrorText(error)}</p>}

      <div className="flex flex-wrap gap-1.5 items-center">
        {FILTERS.map(([k, label]) => {
          const n = k === 'active'
            ? Object.entries(counts).filter(([s]) => s !== 'resolved')
                .reduce((a, [, v]) => a + v.count, 0)
            : k === 'all'
              ? Object.values(counts).reduce((a, v) => a + v.count, 0)
              : counts[k]?.count ?? 0
          return (
            <button key={k} onClick={() => setStatus(k)}
              className={`text-[11px] px-2.5 py-1 rounded-full border transition-colors ${
                status === k ? 'bg-indigo-600/25 border-indigo-500 text-indigo-200'
                             : 'bg-slate-900 border-slate-700 text-slate-400 hover:border-slate-500'}`}>
              {tp(label)} <span className="font-mono">{fa(n)}</span>
            </button>)
        })}
        <label className="text-[11px] text-slate-400 flex items-center gap-1.5 ms-2">
          <input type="checkbox" checked={controlledOnly}
                 onChange={e => setControlledOnly(e.target.checked)} />
          {t('Controlled items only', 'فقط اقلام تحت کنترل')}
        </label>
      </div>

      {isLoading && <p className="text-sm text-slate-400">{t('Loading…', 'در حال بارگذاری…')}</p>}
      {data?.exceptions.length === 0 && (
        <p className="text-[12px] text-slate-500 py-4">
          {t('Nothing in this view. If you have not run the check recently, press “Run the check again”.',
             'موردی در این نما نیست. اگر تازه بررسی نکرده‌اید، «بررسی دوباره» را بزنید.')}
        </p>)}

      <div className="space-y-2">
        {data?.exceptions.map(e => {
          const sev = SEV[e.severity] ?? SEV.info
          const isOpen = openId === e.id
          return (
            <div key={e.id}
                 className={`border rounded-lg ${isOpen ? 'border-slate-600 bg-slate-800/60'
                                                         : 'border-slate-700 bg-slate-800/40'}`}>
              <button onClick={() => setOpenId(isOpen ? null : e.id)}
                      className="w-full flex flex-wrap items-center gap-3 px-4 py-2.5 text-start">
                <span className="font-mono tabular-nums text-lg w-14 text-start shrink-0">
                  {e.score.toFixed(0)}
                </span>
                <span className={`w-2 h-2 rounded-full shrink-0 ${sev.dot}`} />
                <span className="font-medium text-sm">{checkTitle(lang, e.check, e.title_fa)}</span>
                <span className={`text-[10px] px-1.5 py-0.5 rounded border ${sev.chip}`}>{tp(sev.label)}</span>
                {e.is_controlled && (
                  <span className="text-[10px] px-1.5 py-0.5 rounded border bg-rose-500/15 text-rose-300 border-rose-500/40">
                    {t('Controlled', 'تحت کنترل')}
                  </span>)}
                {e.status !== 'open' && (
                  <span className="text-[10px] px-1.5 py-0.5 rounded bg-slate-700 text-slate-300">
                    {STATUS[e.status] ? tp(STATUS[e.status]) : e.status}
                  </span>)}
                <span className="text-[11px] text-slate-500 font-mono">
                  {e.entity_key !== e.check && `${e.entity_key} · `}
                  {t(`${fa(e.row_count)} rows`, `${fa(e.row_count)} ردیف`)}
                  {e.occurrences > 1 && t(` · ${fa(e.occurrences)} occurrences`, ` · تکرار ${fa(e.occurrences)}`)}
                  {e.age_days != null && e.age_days >= 1 &&
                    t(` · ${fa(Math.round(e.age_days))} d`, ` · ${fa(Math.round(e.age_days))} روز`)}
                </span>
                {e.assignee_name && (
                  <span className="text-[11px] text-indigo-300">👤 {e.assignee_name}</span>)}
                <span className="ms-auto text-slate-500 text-xs">{isOpen ? '▲' : '▼'}</span>
              </button>

              {isOpen && detail && detail.id === e.id && (
                <div className="px-4 pb-3 pt-2 border-t border-slate-700/60 space-y-3">
                  <p className="text-[12px] text-slate-400">{detail.detail}</p>
                  {detail.score_breakdown?.explanation && (
                    <p className="text-[11px] text-slate-500">
                      <span className="text-slate-400">{t('Why this rank?', 'چرا این رتبه؟')} </span>
                      <span className="font-mono">{detail.score_breakdown.explanation}</span>
                    </p>)}
                  {detail.remediation && (
                    <p className="text-[12px] text-indigo-300">
                      <span className="text-slate-500">{t('Suggested action', 'اقدام پیشنهادی')}: </span>{detail.remediation}
                    </p>)}
                  {detail.disposition_reason && (
                    <p className="text-[12px] text-amber-300">
                      <span className="text-slate-500">{t('Recorded ruling', 'حکم ثبت‌شده')}: </span>{detail.disposition_reason}
                    </p>)}

                  {detail.affected_rows.length > 0 && (
                    <div>
                      <p className="text-[11px] text-slate-500 pb-1">
                        {t(`Affected rows — all ${fa(detail.affected_rows.length)}`,
                           `ردیف‌های درگیر — همهٔ ${fa(detail.affected_rows.length)} مورد`)}
                      </p>
                      <div className="overflow-x-auto max-h-72 overflow-y-auto border border-slate-700/60 rounded">
                        <table className="w-full text-[11px] font-mono">
                          <thead className="text-slate-500 sticky top-0 bg-slate-800">
                            <tr>{Object.keys(detail.affected_rows[0]).map(k =>
                              <th key={k} className="text-start px-2 py-1 font-normal whitespace-nowrap">{k}</th>)}</tr>
                          </thead>
                          <tbody className="text-slate-300">
                            {detail.affected_rows.map((r, i) => (
                              <tr key={i} className="border-t border-slate-800">
                                {Object.keys(detail.affected_rows[0]).map(k =>
                                  <td key={k} className="px-2 py-1 whitespace-nowrap">
                                    {r[k] == null ? '—' : String(r[k])}
                                  </td>)}
                              </tr>))}
                          </tbody>
                        </table>
                      </div>
                    </div>)}

                  <div className="flex flex-wrap gap-2 text-[12px]">
                    <button onClick={() => simulate(e.id)} disabled={busy}
                      className="px-3 py-1 bg-slate-700 hover:bg-slate-600 rounded disabled:opacity-50">
                      {t('Simulate the fix (changes nothing)', 'شبیه‌سازی اصلاح (بدون تغییر)')}
                    </button>
                    {e.status !== 'accepted' && (
                      <button onClick={() => dispose(e.id, 'accepted')} disabled={busy}
                        className="px-3 py-1 bg-amber-700 hover:bg-amber-600 rounded disabled:opacity-50">
                        {t('Accept with a reason', 'پذیرش با دلیل')}
                      </button>)}
                    {e.status !== 'suppressed' && (
                      <button onClick={() => dispose(e.id, 'suppressed')} disabled={busy}
                        className="px-3 py-1 bg-slate-700 hover:bg-slate-600 rounded disabled:opacity-50">
                        {t('Suppress', 'خاموش کردن')}
                      </button>)}
                    {e.status !== 'resolved' && (
                      <button onClick={() => dispose(e.id, 'resolved')} disabled={busy}
                        className="px-3 py-1 bg-emerald-700 hover:bg-emerald-600 rounded disabled:opacity-50">
                        {t('Mark as resolved', 'علامت‌گذاری به‌عنوان برطرف‌شده')}
                      </button>)}
                  </div>

                  {detail.history.length > 0 && (
                    <div className="text-[11px] text-slate-400 space-y-0.5 pt-1 border-t border-slate-700/60">
                      <p className="text-slate-500 pt-1">{t('Handling history (cannot be overwritten)',
                                                                'سابقهٔ رسیدگی (غیرقابل بازنویسی)')}</p>
                      {detail.history.map((h, i) => (
                        <div key={i} className="flex flex-wrap gap-2">
                          <span className="font-mono text-slate-500">
                            {dateTime(h.at)}
                          </span>
                          <span>{h.event}</span>
                          {h.from && h.to && <span className="text-slate-500">{h.from} → {h.to}</span>}
                          <span className="text-indigo-300">{h.actor}</span>
                          {h.reason && <span className="text-slate-400">— {h.reason}</span>}
                        </div>))}
                    </div>)}
                </div>)}
            </div>)
        })}
      </div>
    </div>
  )
}
