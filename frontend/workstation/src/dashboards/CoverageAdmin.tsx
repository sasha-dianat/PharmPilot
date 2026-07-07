/**
 * CoverageAdmin — دارونامه sources, harvest & review. Per-insurer source cards
 * (probe → detect format → save column overrides), background harvest behind the
 * global proxy lock, staged runs with a diff vs live coverage, and preview→اعمال.
 * The one-shot upload card (instant apply) also lives here, moved from DrugCatalogAdmin.
 */
import { useRef, useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { pricingApi } from '../lib/api'

const fa = (n: number | null | undefined) =>
  n == null ? '—' : new Intl.NumberFormat('fa-IR').format(n)

interface Source {
  id: string; insurer: string; name: string; url: string | null; strategy: string
  settings: Record<string, unknown>; check_interval_days: number; enabled: boolean
  last_run_at: string | null; last_run_status: string | null; due: boolean
  lock_holder: string | null
}
interface RunSummary {
  id: string; source_id: string; insurer: string; status: string
  started_at: string | null; finished_at: string | null
  stats: Record<string, number> | null
  diff_counts: { added: number | null; changed: number | null; removed: number | null }
  error: string | null
}

const STRATEGIES = [
  ['auto', 'تشخیص خودکار'], ['file_url', 'فایل مستقیم (Excel/CSV)'],
  ['html_table', 'جدول HTML'], ['paginated_html', 'HTML صفحه‌بندی‌شده'],
  ['json_api', 'JSON API'],
] as const
const ROLES = ['irc', 'gtin', 'drug_name', 'covered', 'share_pct',
               'reference_price', 'ceiling', 'inpatient', 'ignore'] as const

export default function CoverageAdmin() {
  const qc = useQueryClient()
  const [msg, setMsg] = useState<{ kind: 'ok' | 'err'; text: string } | null>(null)
  const [probe, setProbe] = useState<{ sourceId: string; data: any } | null>(null)
  const [openRun, setOpenRun] = useState<string | null>(null)

  const { data: sources } = useQuery<{ sources: Source[] }>({
    queryKey: ['coverage-sources'],
    queryFn: () => pricingApi.coverageSources().then(r => r.data),
    refetchInterval: 15_000,
  })
  const { data: hs } = useQuery<{ running: boolean; phase: string; pages: number;
                                  rows: number; insurer: string; error: string | null;
                                  run_id: string | null; lock_holder: string | null }>({
    queryKey: ['coverage-harvest-status'],
    queryFn: () => pricingApi.coverageHarvestStatus().then(r => r.data),
    refetchInterval: q => (q.state.data?.running ? 2_000 : 15_000),
  })
  const { data: runs } = useQuery<{ runs: RunSummary[] }>({
    queryKey: ['coverage-runs'],
    queryFn: () => pricingApi.coverageRuns().then(r => r.data),
    refetchInterval: hs?.running ? 5_000 : 30_000,
  })

  const err = (e: unknown, fallback: string) =>
    setMsg({ kind: 'err', text: (e as any)?.response?.data?.detail || fallback })

  const doProbe = async (s: Source) => {
    setMsg(null)
    try {
      const { data } = await pricingApi.coverageProbe(s.id)
      setProbe({ sourceId: s.id, data })
    } catch (e: any) {
      const d = e?.response?.data?.detail
      if (d && typeof d === 'object' && d.diagnostics?.summary) {
        setMsg({ kind: 'err', text: `تشخیص ناموفق [${d.diagnostics.summary.worst_category || '—'}]: ${d.diagnostics.summary.top_hint || d.message}` })
      } else {
        setMsg({ kind: 'err', text: (typeof d === 'string' ? d : 'تشخیص ناموفق بود.') })
      }
    }
  }
  const doHarvest = async (s: Source) => {
    setMsg(null)
    try {
      await pricingApi.coverageHarvest(s.id)
      qc.invalidateQueries({ queryKey: ['coverage-harvest-status'] })
    } catch (e) { err(e, 'شروع برداشت ناموفق بود.') }
  }
  const saveSource = async (s: Source, patch: Record<string, unknown>) => {
    setMsg(null)
    try {
      await pricingApi.coverageSourceSave(s.id, {
        insurer: s.insurer, name: s.name, url: s.url, strategy: s.strategy,
        settings: s.settings, check_interval_days: s.check_interval_days,
        enabled: s.enabled, ...patch,
      })
      qc.invalidateQueries({ queryKey: ['coverage-sources'] })
      setMsg({ kind: 'ok', text: 'ذخیره شد.' })
    } catch (e) { err(e, 'ذخیره ناموفق بود.') }
  }

  const locked = !!(hs?.lock_holder || sources?.sources?.[0]?.lock_holder)

  return (
    <div className="p-4 space-y-4 text-slate-100" dir="rtl">
      <h2 className="text-lg font-bold">پوشش بیمه — دارونامه بیمه‌گرها</h2>
      {msg && <p className={`text-sm ${msg.kind === 'ok' ? 'text-emerald-400' : 'text-red-400'}`}>{msg.text}</p>}

      {/* live harvest strip */}
      {hs?.running && (
        <div className="bg-indigo-500/10 border border-indigo-500/40 rounded-lg p-3 text-[12px] font-mono flex flex-wrap gap-x-5">
          <span className="text-indigo-300">در حال برداشت: {hs.insurer}</span>
          <span>مرحله: {hs.phase}</span><span>صفحات: {fa(hs.pages)}</span>
          <span>ردیف‌ها: {fa(hs.rows)}</span>
          {hs.error && <span className="text-red-400">{hs.error}</span>}
        </div>
      )}

      {/* source cards */}
      <div className="grid md:grid-cols-2 gap-3">
        {(sources?.sources || []).map(s => (
          <SourceCard key={s.id} s={s} locked={locked} lockHolder={hs?.lock_holder ?? s.lock_holder}
                      onProbe={() => doProbe(s)} onHarvest={() => doHarvest(s)}
                      onSave={patch => saveSource(s, patch)} />
        ))}
      </div>

      {/* probe result */}
      {probe && (
        <ProbePanel data={probe.data} onClose={() => setProbe(null)}
          onSaveOverrides={(ov) => {
            const s = sources?.sources.find(x => x.id === probe.sourceId)
            if (s) saveSource(s, { settings: { ...s.settings, column_overrides: ov },
                                   strategy: probe.data.detected_strategy })
            setProbe(null)
          }} />
      )}

      {/* runs + preview */}
      <div className="bg-slate-800/50 border border-slate-700 rounded-lg p-4 space-y-2">
        <p className="font-semibold text-sm">اجراهای برداشت</p>
        {(runs?.runs || []).length === 0 && <p className="text-[12px] text-slate-500">هنوز اجرایی ثبت نشده.</p>}
        {(runs?.runs || []).map(r => (
          <div key={r.id} className="flex flex-wrap items-center gap-3 text-[12px] border-b border-slate-700/60 pb-1.5">
            <span className="font-mono">{r.insurer}</span>
            <StatusChip status={r.status} />
            <span className="text-slate-500">{r.finished_at ? new Date(r.finished_at).toLocaleString('fa-IR') : '…'}</span>
            {r.stats && <span>ردیف: {fa(r.stats.rows)} · اعمال‌پذیر: {fa(r.stats.applied)} · بازبینی: {fa(r.stats.review)}</span>}
            <span className="text-slate-400">＋{fa(r.diff_counts.added)} / ✎{fa(r.diff_counts.changed)} / −{fa(r.diff_counts.removed)}</span>
            {r.error && <span className="text-red-400 truncate max-w-[24rem]">{r.error}</span>}
            {r.status === 'parsed' &&
              <button onClick={() => setOpenRun(openRun === r.id ? null : r.id)}
                className="px-2 py-0.5 bg-slate-700 hover:bg-slate-600 rounded">پیش‌نمایش</button>}
          </div>
        ))}
        {openRun && <RunPreview runId={openRun} onDone={() => { setOpenRun(null)
          qc.invalidateQueries({ queryKey: ['coverage-runs'] }) }} onError={err} />}
      </div>

      <UploadCard onMsg={setMsg} />
    </div>
  )
}

function StatusChip({ status }: { status: string }) {
  const tone: Record<string, string> = {
    parsed: 'bg-amber-500/15 text-amber-300', approved: 'bg-emerald-500/15 text-emerald-300',
    rejected: 'bg-slate-600/40 text-slate-400', failed: 'bg-red-500/15 text-red-300',
    running: 'bg-indigo-500/15 text-indigo-300',
  }
  return <span className={`text-[11px] px-2 py-0.5 rounded-full ${tone[status] || 'bg-slate-700'}`}>{status}</span>
}

function SourceCard({ s, locked, lockHolder, onProbe, onHarvest, onSave }: {
  s: Source; locked: boolean; lockHolder: string | null
  onProbe: () => void; onHarvest: () => void; onSave: (patch: Record<string, unknown>) => void
}) {
  const [url, setUrl] = useState(s.url || '')
  const [strategy, setStrategy] = useState(s.strategy)
  const [interval, setIntervalDays] = useState(s.check_interval_days)
  return (
    <div className="bg-slate-800/50 border border-slate-700 rounded-lg p-3 space-y-2">
      <div className="flex items-center gap-2">
        <span className="font-semibold text-sm">{s.name}</span>
        {s.due && <span className="text-[11px] px-2 py-0.5 rounded-full bg-amber-500/15 text-amber-300">به‌روزرسانی لازم</span>}
        {s.last_run_status && <StatusChip status={s.last_run_status} />}
      </div>
      <input value={url} onChange={e => setUrl(e.target.value)} dir="ltr" placeholder="https://…"
        className="w-full bg-slate-900 border border-slate-600 rounded px-2 py-1 text-[12px] font-mono" />
      <div className="flex flex-wrap items-center gap-2 text-[12px]">
        <select value={strategy} onChange={e => setStrategy(e.target.value)}
          className="bg-slate-900 border border-slate-600 rounded px-2 py-1">
          {STRATEGIES.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
        </select>
        <label className="flex items-center gap-1 text-slate-400">هر
          <input type="number" value={interval} onChange={e => setIntervalDays(+e.target.value)}
            className="w-14 bg-slate-900 border border-slate-600 rounded px-1 py-0.5" /> روز</label>
        <button onClick={() => onSave({ url, strategy, check_interval_days: interval })}
          className="px-3 py-1 bg-slate-700 hover:bg-slate-600 rounded">ذخیره</button>
        <button onClick={onProbe} className="px-3 py-1 bg-indigo-600 hover:bg-indigo-500 rounded">تشخیص</button>
        <button onClick={onHarvest} disabled={locked}
          title={locked ? `قفل برداشت: ${lockHolder}` : undefined}
          className="px-3 py-1 bg-emerald-600 hover:bg-emerald-500 rounded disabled:opacity-40">برداشت</button>
      </div>
      {s.last_run_at && <p className="text-[11px] text-slate-500">
        آخرین اجرا: {new Date(s.last_run_at).toLocaleString('fa-IR')}</p>}
    </div>
  )
}

function ProbePanel({ data, onClose, onSaveOverrides }: {
  data: any; onClose: () => void; onSaveOverrides: (ov: Record<string, string>) => void
}) {
  const cols: string[] = data.sample_rows?.[0] ? Object.keys(data.sample_rows[0]) : []
  const [roles, setRoles] = useState<Record<string, string>>(
    () => ({ ...(data.inferred_columns || {}) }))
  return (
    <div className="bg-slate-800/70 border border-indigo-500/40 rounded-lg p-4 space-y-2">
      <div className="flex items-center gap-3">
        <p className="font-semibold text-sm">نتیجه تشخیص</p>
        <span className="text-[11px] px-2 py-0.5 rounded-full bg-indigo-500/15 text-indigo-300">{data.detected_strategy}</span>
        <button onClick={onClose} className="mr-auto text-slate-400 hover:text-slate-200">بستن ✕</button>
      </div>
      <div className="overflow-x-auto">
        <table className="text-[11px] font-mono">
          <thead><tr>{cols.map(c => (
            <th key={c} className="px-2 py-1 text-right border-b border-slate-600">
              <div className="text-slate-300">{c}</div>
              <select value={roles[c] || 'ignore'}
                onChange={e => setRoles({ ...roles, [c]: e.target.value })}
                className="bg-slate-900 border border-slate-600 rounded px-1 mt-0.5">
                {ROLES.map(r => <option key={r} value={r}>{r}</option>)}
              </select>
            </th>))}</tr></thead>
          <tbody>{(data.sample_rows || []).slice(0, 8).map((row: any, i: number) => (
            <tr key={i}>{cols.map(c => <td key={c} className="px-2 py-0.5 text-slate-400 max-w-[12rem] truncate">{String(row[c] ?? '')}</td>)}</tr>
          ))}</tbody>
        </table>
      </div>
      <button onClick={() => onSaveOverrides(roles)}
        className="px-3 py-1 bg-emerald-600 hover:bg-emerald-500 rounded text-sm">ذخیره تنظیمات ستون‌ها</button>
    </div>
  )
}

function RunPreview({ runId, onDone, onError }: {
  runId: string; onDone: () => void; onError: (e: unknown, f: string) => void
}) {
  const [removeMissing, setRemoveMissing] = useState(false)
  const [accepted, setAccepted] = useState<Set<number>>(new Set())
  const [busy, setBusy] = useState(false)
  const { data: run } = useQuery<any>({
    queryKey: ['coverage-run', runId],
    queryFn: () => pricingApi.coverageRun(runId).then(r => r.data),
  })
  if (!run) return <p className="text-[12px] text-slate-500">در حال بارگذاری…</p>
  const decide = async (approve: boolean) => {
    setBusy(true)
    try {
      if (approve) await pricingApi.coverageApprove(runId,
        { remove_missing: removeMissing, accepted_review_ids: [...accepted] })
      else await pricingApi.coverageReject(runId)
      onDone()
    } catch (e) { onError(e, 'تصمیم اعمال نشد.') } finally { setBusy(false) }
  }
  const d = run.diff || { added: 0, changed: 0, removed: 0, samples: {} }
  return (
    <div className="border border-amber-500/40 rounded-lg p-3 space-y-2 text-[12px]">
      <div className="flex flex-wrap gap-4 font-mono">
        <span>ردیف‌ها: {fa(run.stats?.rows)}</span>
        <span className="text-emerald-300">اعمال‌پذیر: {fa(run.stats?.applied)}</span>
        <span className="text-amber-300">بازبینی: {fa(run.stats?.review)}</span>
        <span className="text-red-300">نامنطبق: {fa(run.stats?.unmatched)}</span>
        <span>＋جدید: {fa(d.added)} · ✎تغییر: {fa(d.changed)} · −حذف‌شده از فهرست: {fa(d.removed)}</span>
      </div>
      {(d.samples?.changed || []).length > 0 && (
        <div className="max-h-40 overflow-y-auto space-y-0.5">
          {(d.samples.changed).map((c: any) => (
            <div key={c.irc} className="font-mono text-slate-400">
              {c.irc}: {c.fields.map((f: any) => `${f.field} ${f.old ?? '—'}→${f.new ?? '—'}`).join(' · ')}
            </div>))}
        </div>)}
      {run.diagnostics?.summary && (run.diagnostics.summary.failed > 0 || (run.diagnostics.attempts||[]).length > 0) && (
        <details className="border border-slate-700 rounded p-2">
          <summary className="cursor-pointer text-slate-300">
            تشخیص خطاها — {fa(run.diagnostics.summary.failed)} ناموفق از {fa(run.diagnostics.summary.total)}
            {run.diagnostics.summary.worst_category && ` · ${run.diagnostics.summary.worst_category}`}
          </summary>
          {run.diagnostics.summary.top_hint && <p className="text-amber-300 mt-1">{run.diagnostics.summary.top_hint}</p>}
          <div className="max-h-48 overflow-y-auto space-y-1 mt-1">
            {(run.diagnostics.attempts || []).filter((a: any) => a.severity !== 'ok').map((a: any, i: number) => (
              <div key={i} className="font-mono text-[11px] text-slate-400 border-b border-slate-700/50 pb-1">
                <span className={a.severity === 'error' ? 'text-red-300' : 'text-amber-300'}>[{a.category}]</span>{' '}
                HTTP {a.status} · {a.url}
                {a.exception && <div className="text-red-400">{a.exception}</div>}
                <div className="text-slate-500">{a.hint}</div>
                {a.body_snippet && <details><summary className="cursor-pointer text-slate-600">بدنهٔ پاسخ</summary>
                  <pre className="whitespace-pre-wrap text-slate-500">{a.body_snippet.slice(0, 500)}</pre></details>}
              </div>))}
          </div>
        </details>)}
      {(run.review || []).length > 0 && (
        <div className="max-h-40 overflow-y-auto space-y-0.5">
          <p className="font-semibold">موارد نیازمند بازبینی — تأیید هر مورد آن را همراه اجرا اعمال می‌کند:</p>
          {run.review.map((item: any) => (
            <label key={item.id} className="flex items-center gap-2 font-mono text-slate-400">
              <input type="checkbox" checked={accepted.has(item.id)}
                onChange={e => { const s = new Set(accepted); e.target.checked ? s.add(item.id) : s.delete(item.id); setAccepted(s) }} />
              {item.name} ← «{String(item.row?.drug_name ?? '')}» (اطمینان {item.confidence})
            </label>))}
        </div>)}
      <label className="flex items-center gap-2 text-amber-300">
        <input type="checkbox" checked={removeMissing} onChange={e => setRemoveMissing(e.target.checked)} />
        حذف پوشش اقلامی که در فهرست جدید نیستند (حداکثر ۵۰ مورد نمونه‌گیری‌شده — با احتیاط)
      </label>
      <div className="flex gap-2">
        <button onClick={() => decide(true)} disabled={busy}
          className="px-4 py-1.5 bg-emerald-600 hover:bg-emerald-500 rounded disabled:opacity-50">اعمال</button>
        <button onClick={() => decide(false)} disabled={busy}
          className="px-4 py-1.5 bg-red-600 hover:bg-red-500 rounded disabled:opacity-50">رد</button>
      </div>
    </div>
  )
}

function UploadCard({ onMsg }: { onMsg: (m: { kind: 'ok' | 'err'; text: string }) => void }) {
  const qc = useQueryClient()
  const covRef = useRef<HTMLInputElement>(null)
  const [covInsurer, setCovInsurer] = useState('tamin')
  const [busy, setBusy] = useState(false)
  const importCoverage = async (file: File) => {
    setBusy(true)
    try {
      const { data } = await pricingApi.importCoverage(file, covInsurer)
      onMsg({ kind: 'ok', text: `پوشش بیمه برای ${fa(data.stats.products_updated)} قلم اعمال شد (${fa(data.stats.review)} بازبینی، ${fa(data.stats.unmatched)} نامنطبق).` })
      qc.invalidateQueries({ queryKey: ['coverage-runs'] })
    } catch (e: unknown) {
      onMsg({ kind: 'err', text: (e as any)?.response?.data?.detail || 'بارگذاری دارونامه ناموفق بود.' })
    } finally { setBusy(false); if (covRef.current) covRef.current.value = '' }
  }
  return (
    <div className="bg-slate-800/50 border border-slate-700 rounded-lg p-4 space-y-2">
      <p className="font-semibold text-sm">بارگذاری دستی دارونامه (اعمال فوری)</p>
      <p className="text-[11px] text-slate-500">فایل Excel/CSV یا صفحه HTML ذخیره‌شده — موارد کم‌اطمینان اعمال نمی‌شوند.</p>
      <div className="flex items-center gap-3 text-sm">
        <select value={covInsurer} onChange={e => setCovInsurer(e.target.value)} disabled={busy}
          className="bg-slate-900 border border-slate-600 rounded px-2 py-1">
          <option value="tamin">تأمین اجتماعی</option>
          <option value="salamat">بیمه سلامت</option>
          <option value="armed_forces">نیروهای مسلح</option>
        </select>
        <input ref={covRef} type="file" accept=".xlsx,.xls,.csv,.tsv,.html,.htm" className="text-sm text-slate-300"
          onChange={e => { const f = e.target.files?.[0]; if (f) importCoverage(f) }} disabled={busy} />
      </div>
    </div>
  )
}
