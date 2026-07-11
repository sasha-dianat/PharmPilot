/**
 * CoverageAdmin — دارونامه sources, harvest & review. Per-insurer source cards
 * (probe → detect format → save column overrides), background harvest behind the
 * global proxy lock, staged runs with a diff vs live coverage, and preview→اعمال.
 * The one-shot upload card (instant apply) also lives here, moved from DrugCatalogAdmin.
 */
import { useRef, useState, type ReactNode } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { pricingApi, apiErrorText } from '../lib/api'

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
  const [showIncons, setShowIncons] = useState(false)

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
    setMsg({ kind: 'err', text: apiErrorText(e, fallback) })

  const doProbe = async (s: Source) => {
    setMsg(null)
    try {
      const { data } = await pricingApi.coverageProbe(s.id)
      setProbe({ sourceId: s.id, data })
    } catch (e) {
      // apiErrorText renders the classified probe failure ([category] hint) too
      err(e, 'تشخیص ناموفق بود.')
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
      <div className="flex items-center gap-3 flex-wrap">
        <h2 className="text-lg font-bold">پوشش بیمه — دارونامه بیمه‌گرها</h2>
        <button onClick={() => setShowIncons(v => !v)}
          className={`px-3 py-1.5 text-sm rounded-md border transition-colors ${
            showIncons ? 'bg-indigo-600 border-indigo-500 hover:bg-indigo-500'
                       : 'bg-slate-700 border-slate-600 hover:bg-slate-600'}`}>
          بازبینی ناسازگاری‌ها
        </button>
      </div>
      {msg && <p className={`text-sm ${msg.kind === 'ok' ? 'text-emerald-400' : 'text-red-400'}`}>{msg.text}</p>}

      {showIncons && <InconsistenciesPanel onError={err} />}

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
    // رد discards the WHOLE run (checked review items are NOT applied) — this
    // was mis-clicked as "apply the checked items" once, hence the guard.
    if (!approve && !window.confirm(
      'کل این اجرا رد می‌شود و هیچ پوششی اعمال نمی‌شود — حتی موارد تأییدشده. ادامه؟')) return
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
          <div className="flex flex-wrap items-center gap-2">
            <p className="font-semibold">موارد نیازمند بازبینی — تأیید هر مورد آن را همراه اجرا اعمال می‌کند:</p>
            <button onClick={() => setAccepted(new Set((run.review || []).map((i: any) => i.id)))}
              className="px-2 py-0.5 bg-slate-700 hover:bg-slate-600 rounded text-[11px]">
              تأیید همه ({fa((run.review || []).length)})
            </button>
            <button onClick={() => setAccepted(new Set())}
              className="px-2 py-0.5 bg-slate-700 hover:bg-slate-600 rounded text-[11px]">لغو همه</button>
          </div>
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
      <div className="flex items-center gap-2">
        <button onClick={() => decide(true)} disabled={busy}
          className="px-4 py-1.5 bg-emerald-600 hover:bg-emerald-500 rounded disabled:opacity-50">
          اعمال اجرا{accepted.size > 0 ? ` (+${fa(accepted.size)} مورد تأییدشده)` : ''}
        </button>
        <button onClick={() => decide(false)} disabled={busy}
          className="px-4 py-1.5 bg-red-600/70 hover:bg-red-500 rounded disabled:opacity-50">رد کل اجرا</button>
        <span className="text-[11px] text-slate-500">موارد تأییدشده فقط همراه «اعمال اجرا» اعمال می‌شوند.</span>
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
      onMsg({ kind: 'err', text: apiErrorText(e, 'بارگذاری دارونامه ناموفق بود.') })
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

// ── Inconsistencies review (read-only data-quality surface) ──────────────────
interface IncData {
  insurer: string
  coverage: {
    counts: { unmatched: number; review: number; price_conflicts: number }
    unmatched: any[]
    review: any[]
    price_conflicts: { irc: string; name_fa: string; announced_price: number;
                       reference_price: number; gap_pct: number }[]
  }
  nfi: {
    counts: { no_price: number; no_generic: number; no_country: number;
              no_atc: number; total: number }
    no_price: { irc: string; name_fa: string }[]
    no_generic: { irc: string; name_fa: string }[]
    no_country: { irc: string; name_fa: string }[]
    no_atc: { irc: string; name_fa: string }[]
  }
}

const INSURERS = [['tamin', 'تأمین اجتماعی'], ['salamat', 'بیمه سلامت'],
                  ['armed_forces', 'نیروهای مسلح']] as const

function Badge({ n, active }: { n: number; active?: boolean }) {
  const tone = n === 0 ? 'bg-emerald-500/15 text-emerald-300'
             : active ? 'bg-slate-900/40 text-slate-100' : 'bg-slate-700 text-slate-200'
  return <span className={`text-xs px-2 py-0.5 rounded-full tabular-nums ${tone}`}>{fa(n)}</span>
}

function Empty({ text = 'هیچ ناسازگاری‌ای یافت نشد ✓' }: { text?: string }) {
  return (
    <div className="text-sm text-emerald-300 bg-emerald-500/5 border border-emerald-500/20 rounded-md px-3 py-2.5">
      {text}
    </div>
  )
}

function Section({ title, count, children, empty }:
  { title: string; count: number; children: ReactNode; empty?: string }) {
  return (
    <section className="space-y-2">
      <div className="flex items-center gap-2">
        <h4 className="text-sm font-semibold text-slate-200">{title}</h4>
        <Badge n={count} />
      </div>
      {count > 0 ? children : <Empty text={empty} />}
    </section>
  )
}

function ScrollTable({ head, children }: { head: ReactNode; children: ReactNode }) {
  return (
    <div className="max-h-72 overflow-y-auto rounded-md border border-slate-700/60">
      <table className="w-full text-sm">
        <thead className="sticky top-0 bg-slate-800 text-xs text-slate-400 shadow-sm">{head}</thead>
        <tbody className="divide-y divide-slate-700/40">{children}</tbody>
      </table>
    </div>
  )
}

// generic IRC | نام table (used by every NFI section)
function IrcNameTable({ items }: { items: { irc: string; name_fa: string }[] }) {
  return (
    <ScrollTable head={
      <tr><th className="px-3 py-2 text-right font-medium">IRC</th>
          <th className="px-3 py-2 text-right font-medium">نام</th></tr>}>
      {items.map((it, i) => (
        <tr key={`${it.irc}-${i}`} className="hover:bg-slate-700/20">
          <td className="px-3 py-2 text-right tabular-nums font-mono text-slate-400 whitespace-nowrap">{it.irc}</td>
          <td className="px-3 py-2 text-right text-slate-200">{it.name_fa}</td>
        </tr>))}
    </ScrollTable>
  )
}

function InconsistenciesPanel({ onError }: { onError: (e: unknown, f: string) => void }) {
  const [insurer, setInsurer] = useState('salamat')
  const [threshold, setThreshold] = useState(25)
  const [tab, setTab] = useState<'coverage' | 'nfi'>('coverage')

  const { data, isFetching, isError, error } = useQuery<IncData>({
    queryKey: ['inconsistencies', insurer, threshold],
    queryFn: () => pricingApi.inconsistencies(insurer, threshold).then(r => r.data),
  })
  if (isError) onError(error, 'دریافت ناسازگاری‌ها ناموفق بود.')

  const cov = data?.coverage
  const nfi = data?.nfi
  const covTotal = cov ? cov.counts.unmatched + cov.counts.review + cov.counts.price_conflicts : 0
  const nfiTotal = nfi ? nfi.counts.no_price + nfi.counts.no_generic + nfi.counts.no_country + nfi.counts.no_atc : 0
  const gapTone = (g: number) =>
    Math.abs(g) >= 50 ? 'text-red-300' : Math.abs(g) >= 25 ? 'text-amber-300' : 'text-slate-300'

  return (
    <div className="bg-slate-800/50 border border-indigo-500/30 rounded-lg p-4 space-y-3">
      {/* controls */}
      <div className="flex flex-wrap items-center gap-3">
        <label className="flex items-center gap-1.5 text-sm text-slate-400">بیمه‌گر
          <select value={insurer} onChange={e => setInsurer(e.target.value)}
            className="bg-slate-900 border border-slate-600 rounded px-2 py-1 text-slate-100">
            {INSURERS.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
          </select>
        </label>
        <label className="flex items-center gap-1.5 text-sm text-slate-400">آستانهٔ اختلاف قیمت (٪)
          <input type="number" min={0} value={threshold}
            onChange={e => setThreshold(Math.max(0, +e.target.value))}
            className="w-20 bg-slate-900 border border-slate-600 rounded px-2 py-1 text-slate-100 tabular-nums" />
        </label>
        {isFetching && <span className="text-xs text-indigo-300">در حال بارگذاری…</span>}
      </div>

      {/* tabs */}
      <div className="flex items-center gap-2 border-b border-slate-700">
        {([['coverage', 'پوشش بیمه', covTotal], ['nfi', 'کاتالوگ NFI', nfiTotal]] as const).map(([key, label, n]) => (
          <button key={key} onClick={() => setTab(key)}
            className={`flex items-center gap-2 px-3 py-2 text-sm rounded-t-md border-b-2 -mb-px transition-colors ${
              tab === key ? 'border-indigo-400 text-slate-100' : 'border-transparent text-slate-400 hover:text-slate-200'}`}>
            {label} <Badge n={n} active={tab === key} />
          </button>))}
      </div>

      {!data ? <p className="text-sm text-slate-500 py-4">در حال بارگذاری…</p> : tab === 'coverage' ? (
        <div className="space-y-5">
          <Section title="نامنطبق (بدون تطبیق در کاتالوگ)" count={cov!.counts.unmatched}
                   empty="همهٔ ردیف‌های دارونامه تطبیق داده شدند ✓">
            <ScrollTable head={
              <tr><th className="px-3 py-2 text-right font-medium">نام در دارونامه</th>
                  <th className="px-3 py-2 text-left font-medium">اطمینان</th></tr>}>
              {cov!.unmatched.map((it, i) => (
                <tr key={i} className="hover:bg-slate-700/20">
                  <td className="px-3 py-2 text-right text-slate-200">{String(it.row?.drug_name ?? it.row?.name ?? '—')}</td>
                  <td className="px-3 py-2 text-left tabular-nums text-slate-400">{fa(it.confidence)}</td>
                </tr>))}
            </ScrollTable>
          </Section>

          <Section title="نیازمند بازبینی (تطبیق کم‌اطمینان)" count={cov!.counts.review}
                   empty="موردی برای بازبینی نیست ✓">
            <ScrollTable head={
              <tr><th className="px-3 py-2 text-right font-medium">کاندیدای کاتالوگ</th>
                  <th className="px-3 py-2 text-right font-medium">نام در دارونامه</th>
                  <th className="px-3 py-2 text-left font-medium">اطمینان</th></tr>}>
              {cov!.review.map((it, i) => (
                <tr key={it.id ?? i} className="hover:bg-slate-700/20">
                  <td className="px-3 py-2 text-right text-slate-200">{String(it.name ?? '—')}</td>
                  <td className="px-3 py-2 text-right text-slate-400">{String(it.row?.drug_name ?? '—')}</td>
                  <td className="px-3 py-2 text-left tabular-nums text-slate-400">{fa(it.confidence)}</td>
                </tr>))}
            </ScrollTable>
          </Section>

          <Section title="مغایرت قیمت (قیمت مرجع بیمه در برابر قیمت اعلامی)" count={cov!.counts.price_conflicts}
                   empty="هیچ مغایرت قیمتی بالاتر از آستانه یافت نشد ✓">
            <ScrollTable head={
              <tr><th className="px-3 py-2 text-right font-medium">نام</th>
                  <th className="px-3 py-2 text-left font-medium">قیمت اعلامی</th>
                  <th className="px-3 py-2 text-left font-medium">قیمت مرجع بیمه</th>
                  <th className="px-3 py-2 text-left font-medium">اختلاف٪</th></tr>}>
              {cov!.price_conflicts.map((c, i) => (
                <tr key={`${c.irc}-${i}`} className="hover:bg-slate-700/20">
                  <td className="px-3 py-2 text-right text-slate-200">{c.name_fa}</td>
                  <td className="px-3 py-2 text-left tabular-nums text-slate-300 whitespace-nowrap">{fa(c.announced_price)}</td>
                  <td className="px-3 py-2 text-left tabular-nums text-slate-300 whitespace-nowrap">{fa(c.reference_price)}</td>
                  <td className={`px-3 py-2 text-left tabular-nums font-semibold whitespace-nowrap ${gapTone(c.gap_pct)}`}>
                    {c.gap_pct > 0 ? '+' : ''}{fa(c.gap_pct)}٪
                  </td>
                </tr>))}
            </ScrollTable>
          </Section>
        </div>
      ) : (
        <div className="space-y-5">
          <p className="text-xs text-slate-500">
            مجموع اقلام کاتالوگ: <span className="tabular-nums text-slate-300">{fa(nfi!.counts.total)}</span>
            {' '}· نمونهٔ حداکثر ۱۰۰ مورد در هر بخش
          </p>
          <Section title="بدون قیمت اعلامی" count={nfi!.counts.no_price} empty="همهٔ اقلام قیمت اعلامی دارند ✓">
            <IrcNameTable items={nfi!.no_price} />
          </Section>
          <Section title="بدون نام ژنریک" count={nfi!.counts.no_generic} empty="همهٔ اقلام نام ژنریک دارند ✓">
            <IrcNameTable items={nfi!.no_generic} />
          </Section>
          <Section title="بدون کشور سازنده" count={nfi!.counts.no_country} empty="همهٔ اقلام کشور سازنده دارند ✓">
            <IrcNameTable items={nfi!.no_country} />
          </Section>
          <Section title="بدون کد ATC" count={nfi!.counts.no_atc} empty="همهٔ اقلام کد ATC دارند ✓">
            <IrcNameTable items={nfi!.no_atc} />
          </Section>
        </div>
      )}
    </div>
  )
}
