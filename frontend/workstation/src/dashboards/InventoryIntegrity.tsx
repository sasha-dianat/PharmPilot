/**
 * InventoryIntegrity — «سلامت موجودی»
 *
 * The reconciliation report, the pending write-off queue, and the ledger chain
 * verification in one place. This panel answers one question: can the stock
 * numbers be trusted right now, and if not, exactly which rows are wrong.
 *
 * It deliberately offers no "fix it" button. Every repair is an approved,
 * recorded movement — a report that can silently mutate stock is how an audit
 * trail stops meaning anything.
 */
import { useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { inventoryIntegrityApi, apiErrorText } from '../lib/api'

interface Finding {
  check: string; severity: 'critical' | 'high' | 'medium' | 'info'
  count: number; title_fa: string; detail: string
  samples: Record<string, unknown>[]; remediation: string
}
interface Report {
  healthy: boolean; trustworthy: boolean; blocking: boolean
  checks_run: number; checks_firing: number; total_rows_affected: number
  by_severity: Record<string, number>; findings: Finding[]; generated_at: string
}
interface Approval {
  id: string; irc: string | null; ndc11: string | null; movement_type: string
  quantity: number; reason: string; is_controlled: boolean; status: string
  requested_by: string; created_at: string | null
}

const fa = (n: number) => new Intl.NumberFormat('fa-IR').format(n)

const SEV: Record<string, { chip: string; dot: string; label: string }> = {
  critical: { chip: 'bg-rose-500/15 text-rose-300 border-rose-500/40', dot: 'bg-rose-400', label: 'بحرانی' },
  high:     { chip: 'bg-amber-500/15 text-amber-300 border-amber-500/40', dot: 'bg-amber-400', label: 'مهم' },
  medium:   { chip: 'bg-sky-500/15 text-sky-300 border-sky-500/40', dot: 'bg-sky-400', label: 'متوسط' },
  info:     { chip: 'bg-slate-600/20 text-slate-300 border-slate-600', dot: 'bg-slate-400', label: 'اطلاعی' },
}

export default function InventoryIntegrity() {
  const qc = useQueryClient()
  const [open, setOpen] = useState<string | null>(null)
  const [msg, setMsg] = useState<{ kind: 'ok' | 'err'; text: string } | null>(null)
  const [busy, setBusy] = useState(false)

  const { data: report, isLoading, error } = useQuery<Report>({
    queryKey: ['inv-reconciliation'],
    queryFn: () => inventoryIntegrityApi.reconciliation().then(r => r.data),
    refetchInterval: 60_000,
  })
  const { data: approvals } = useQuery<{ approvals: Approval[]; count: number }>({
    queryKey: ['inv-approvals'],
    queryFn: () => inventoryIntegrityApi.approvals('pending').then(r => r.data),
    refetchInterval: 30_000,
  })

  const decide = async (id: string, approve: boolean) => {
    setBusy(true); setMsg(null)
    try {
      await inventoryIntegrityApi.decideApproval(id, { approve })
      setMsg({ kind: 'ok', text: approve ? 'تأیید شد و در دفتر ثبت گردید.' : 'رد شد؛ موجودی تغییری نکرد.' })
      qc.invalidateQueries({ queryKey: ['inv-approvals'] })
      qc.invalidateQueries({ queryKey: ['inv-reconciliation'] })
    } catch (e: unknown) {
      setMsg({ kind: 'err', text: apiErrorText(e, 'ثبت تصمیم ناموفق بود.') })
    } finally { setBusy(false) }
  }

  const verdict = !report ? null
    : report.blocking ? { text: 'دفتر موجودی قابل اتکا نیست', cls: 'text-rose-300', ring: 'ring-rose-500/40' }
    : !report.trustworthy ? { text: 'ایراد مهم دارد', cls: 'text-amber-300', ring: 'ring-amber-500/40' }
    : !report.healthy ? { text: 'قابل اتکا، با نکات جزئی', cls: 'text-sky-300', ring: 'ring-sky-500/40' }
    : { text: 'سالم', cls: 'text-emerald-300', ring: 'ring-emerald-500/40' }

  return (
    <div className="p-4 space-y-4 text-slate-100" dir="rtl">
      <div className="flex flex-wrap items-baseline gap-3">
        <h2 className="text-lg font-bold">سلامت موجودی — تطبیق، شمارش و تأییدها</h2>
        {report && (
          <span className="text-[11px] text-slate-500">
            آخرین بررسی: {new Date(report.generated_at).toLocaleString('fa-IR')}
          </span>)}
      </div>
      {msg && <p className={`text-sm ${msg.kind === 'ok' ? 'text-emerald-400' : 'text-red-400'}`}>{msg.text}</p>}
      {error && <p className="text-sm text-red-400">{apiErrorText(error)}</p>}
      {isLoading && <p className="text-sm text-slate-400">در حال بررسی…</p>}

      {report && verdict && (
        <>
          <div className={`bg-slate-800/50 border border-slate-700 ring-1 ${verdict.ring} rounded-lg p-4
                           flex flex-wrap items-center gap-x-8 gap-y-2`}>
            <div>
              <div className="text-[10px] text-slate-500">وضعیت</div>
              <div className={`text-xl font-bold ${verdict.cls}`}>{verdict.text}</div>
            </div>
            <Metric label="بررسی‌های انجام‌شده" value={fa(report.checks_run)} />
            <Metric label="بررسی‌های هشداردهنده" value={fa(report.checks_firing)} />
            <Metric label="ردیف‌های درگیر" value={fa(report.total_rows_affected)} />
            <Metric label="در انتظار تأیید" value={fa(approvals?.count ?? 0)} />
          </div>

          {/* ── Findings ─────────────────────────────────────────────────── */}
          <div className="space-y-2">
            {report.findings.map(f => {
              const sev = SEV[f.severity] ?? SEV.info
              const expanded = open === f.check
              return (
                <div key={f.check}
                     className={`border rounded-lg ${f.count ? 'border-slate-700 bg-slate-800/50' : 'border-slate-800 bg-slate-900/40'}`}>
                  <button onClick={() => setOpen(expanded ? null : f.check)}
                          className="w-full flex items-center gap-3 px-4 py-2.5 text-right">
                    <span className={`w-2 h-2 rounded-full shrink-0 ${f.count ? sev.dot : 'bg-emerald-500/60'}`} />
                    <span className="font-medium text-sm">{f.title_fa}</span>
                    <span className={`text-[10px] px-1.5 py-0.5 rounded border ${sev.chip}`}>{sev.label}</span>
                    <span className="mr-auto font-mono text-sm tabular-nums">
                      {f.count ? <span className="text-slate-200">{fa(f.count)}</span>
                               : <span className="text-emerald-400">۰</span>}
                    </span>
                    <span className="text-slate-500 text-xs">{expanded ? '▲' : '▼'}</span>
                  </button>
                  {expanded && (
                    <div className="px-4 pb-3 space-y-2 border-t border-slate-700/60 pt-2">
                      <p className="text-[12px] text-slate-400">{f.detail}</p>
                      {f.remediation && (
                        <p className="text-[12px] text-indigo-300">
                          <span className="text-slate-500">اقدام پیشنهادی: </span>{f.remediation}
                        </p>)}
                      {f.samples.length > 0 && (
                        <div className="overflow-x-auto">
                          <table className="text-[11px] font-mono w-full">
                            <thead className="text-slate-500">
                              <tr>{Object.keys(f.samples[0]).map(k =>
                                <th key={k} className="text-right pl-4 pb-1 font-normal">{k}</th>)}</tr>
                            </thead>
                            <tbody className="text-slate-300">
                              {f.samples.map((s, i) => (
                                <tr key={i} className="border-t border-slate-800">
                                  {Object.keys(f.samples[0]).map(k =>
                                    <td key={k} className="pl-4 py-1 whitespace-nowrap">
                                      {s[k] === null || s[k] === undefined ? '—' : String(s[k])}
                                    </td>)}
                                </tr>))}
                            </tbody>
                          </table>
                          {f.count > f.samples.length && (
                            <p className="text-[10px] text-slate-500 pt-1">
                              نمایش {fa(f.samples.length)} از {fa(f.count)} ردیف
                            </p>)}
                        </div>)}
                    </div>)}
                </div>)
            })}
          </div>
        </>
      )}

      {/* ── Pending write-offs ───────────────────────────────────────────── */}
      <div className="bg-slate-800/50 border border-slate-700 rounded-lg p-4 space-y-2">
        <div className="flex items-center gap-2">
          <span className="font-semibold text-sm">تأییدهای در انتظار (کسر موجودی)</span>
          <span className="text-[11px] text-slate-500">
            درخواست‌کننده نمی‌تواند درخواست خود را تأیید کند؛ اقلام تحت کنترل به شاهد نیاز دارند.
          </span>
        </div>
        {!approvals?.count && <p className="text-[12px] text-slate-500">موردی در انتظار نیست.</p>}
        {approvals?.approvals.map(a => (
          <div key={a.id} className="flex flex-wrap items-center gap-3 border-t border-slate-700/60 pt-2 text-[12px]">
            <span className="font-mono text-slate-300">{a.movement_type}</span>
            {a.is_controlled && (
              <span className="text-[10px] px-1.5 py-0.5 rounded bg-rose-500/15 text-rose-300 border border-rose-500/40">
                تحت کنترل
              </span>)}
            <span className="font-mono tabular-nums">{fa(a.quantity)}</span>
            <span className="text-slate-400">{a.irc || a.ndc11}</span>
            <span className="text-slate-500 truncate max-w-md">{a.reason}</span>
            <div className="mr-auto flex gap-2">
              <button onClick={() => decide(a.id, true)} disabled={busy}
                      className="px-3 py-1 bg-emerald-600 hover:bg-emerald-500 rounded disabled:opacity-50">
                تأیید
              </button>
              <button onClick={() => decide(a.id, false)} disabled={busy}
                      className="px-3 py-1 bg-slate-700 hover:bg-slate-600 rounded disabled:opacity-50">
                رد
              </button>
            </div>
          </div>))}
      </div>
    </div>
  )
}

function Metric({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <div className="text-[10px] text-slate-500">{label}</div>
      <div className="text-lg font-bold tabular-nums">{value}</div>
    </div>
  )
}
