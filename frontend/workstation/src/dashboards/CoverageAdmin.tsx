/**
 * CoverageAdmin — دارونامه sources, harvest & review. Per-insurer source cards
 * (probe → detect format → save column overrides), background harvest behind the
 * global proxy lock, staged runs with a diff vs live coverage, and preview→اعمال.
 * The one-shot upload card (instant apply) also lives here, moved from DrugCatalogAdmin.
 */
import { useMemo, useRef, useState, type ReactNode } from 'react'
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
  const [showEnrich, setShowEnrich] = useState(false)

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
        <button onClick={() => setShowEnrich(v => !v)}
          className={`px-3 py-1.5 text-sm rounded-md border transition-colors ${
            showEnrich ? 'bg-fuchsia-600 border-fuchsia-500 hover:bg-fuchsia-500'
                       : 'bg-slate-700 border-slate-600 hover:bg-slate-600'}`}>
          ✨ غنی‌سازی هوشمند
        </button>
      </div>
      {msg && <p className={`text-sm ${msg.kind === 'ok' ? 'text-emerald-400' : 'text-red-400'}`}>{msg.text}</p>}

      {showIncons && <InconsistenciesPanel onError={err} />}
      {showEnrich && <EnrichmentPanel onMsg={setMsg} onError={err} />}

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

// ── Discrepancy Workbench ────────────────────────────────────────────────────
// Per-drug detail from GET /pricing/inconsistencies/drug/{irc}?insurer=
interface DrugDetail {
  irc: string
  catalog: {
    irc: string; name_fa: string | null; name_en: string | null
    generic_name: string | null; ingredient_key: string | null
    strength: string | null; dosage_form: string | null; brand_name: string | null
    manufacturer: string | null; country: string | null; atc: string | null
    announced_price: number | null; package_count: number | null
    gtin: string | null; source: string | null
  } | null
  coverage: Record<string, {
    covered: boolean | null; share_pct: number | null; reference_price: number | null
    ceiling: number | null; inpatient: boolean | null
    match_confidence: number | null; match_method: string | null
  }>
  siblings: { irc: string; name_fa: string; strength: string | null
              dosage_form: string | null; announced_price: number | null
              reference_price: number | null }[]
  analysis: string[]
}

// The six drug-anchored issue types (order = chip order). label + list badge tone.
const ISSUE_TYPES = [
  ['price', 'مغایرت قیمت'], ['review', 'نیازمند بازبینی'], ['no_price', 'بدون قیمت'],
  ['no_generic', 'بدون ژنریک'], ['no_country', 'بدون کشور'], ['no_atc', 'بدون ATC'],
] as const
type IssueType = (typeof ISSUE_TYPES)[number][0]
const ISSUE_LABEL = Object.fromEntries(ISSUE_TYPES) as Record<IssueType, string>

interface FlaggedDrug {
  irc: string; name: string; issues: Set<IssueType>
  announced: number | null; reference: number | null; gap: number | null
}

const gapTone = (g: number | null) =>
  g == null ? 'text-slate-400'
  : Math.abs(g) >= 50 ? 'text-red-300' : Math.abs(g) >= 25 ? 'text-amber-300' : 'text-slate-300'

// color-graded issue badge (price red/amber by |gap|, others slate)
function issueTone(issue: IssueType, gap: number | null): string {
  if (issue === 'price') {
    return Math.abs(gap ?? 0) >= 50
      ? 'bg-red-500/20 text-red-300 border-red-500/40'
      : 'bg-amber-500/20 text-amber-300 border-amber-500/40'
  }
  return 'bg-slate-700/50 text-slate-300 border-slate-600/50'
}

function InconsistenciesPanel({ onError }: { onError: (e: unknown, f: string) => void }) {
  const [insurer, setInsurer] = useState('salamat')
  const [threshold, setThreshold] = useState(25)
  const [search, setSearch] = useState('')
  const [activeChips, setActiveChips] = useState<Set<IssueType>>(new Set())
  const [selectedIrc, setSelectedIrc] = useState<string | null>(null)

  const { data, isFetching, isError, error } = useQuery<IncData>({
    queryKey: ['inconsistencies', insurer, threshold],
    queryFn: () => pricingApi.inconsistencies(insurer, threshold).then(r => r.data),
  })
  if (isError) onError(error, 'دریافت ناسازگاری‌ها ناموفق بود.')

  // ── client-side JOIN: one flagged-drug list keyed by irc, unioning every
  //    drug-anchored issue (price_conflicts, review, and the four nfi.* lists).
  const flagged = useMemo<FlaggedDrug[]>(() => {
    const m = new Map<string, FlaggedDrug>()
    const at = (irc: string, name?: string | null) => {
      let f = m.get(irc)
      if (!f) { f = { irc, name: name || irc, issues: new Set(), announced: null, reference: null, gap: null }; m.set(irc, f) }
      else if (name && (f.name === f.irc)) f.name = name
      return f
    }
    const cov = data?.coverage, nfi = data?.nfi
    cov?.price_conflicts.forEach(c => {
      const f = at(c.irc, c.name_fa); f.issues.add('price')
      f.announced = c.announced_price; f.reference = c.reference_price; f.gap = c.gap_pct
    })
    cov?.review.forEach((r: any) => { if (r?.irc) at(String(r.irc), r.name).issues.add('review') })
    nfi?.no_price.forEach(x => at(x.irc, x.name_fa).issues.add('no_price'))
    nfi?.no_generic.forEach(x => at(x.irc, x.name_fa).issues.add('no_generic'))
    nfi?.no_country.forEach(x => at(x.irc, x.name_fa).issues.add('no_country'))
    nfi?.no_atc.forEach(x => at(x.irc, x.name_fa).issues.add('no_atc'))
    return [...m.values()]
  }, [data])

  const chipCounts = useMemo(() => {
    const c = { price: 0, review: 0, no_price: 0, no_generic: 0, no_country: 0, no_atc: 0 } as Record<IssueType, number>
    flagged.forEach(f => f.issues.forEach(i => { c[i] += 1 }))
    return c
  }, [flagged])

  const shown = useMemo(() => {
    const q = search.trim().toLowerCase()
    const chips = [...activeChips]
    return flagged
      .filter(f => {
        if (chips.length && !chips.every(c => f.issues.has(c))) return false          // AND across chips
        if (q && !(f.irc.includes(q) || f.name.toLowerCase().includes(q))) return false
        return true
      })
      .sort((a, b) => (Math.abs(b.gap ?? -1) - Math.abs(a.gap ?? -1)) || (b.issues.size - a.issues.size))
  }, [flagged, search, activeChips])

  const toggleChip = (k: IssueType) =>
    setActiveChips(prev => { const n = new Set(prev); n.has(k) ? n.delete(k) : n.add(k); return n })

  const unmatched: any[] = data?.coverage.unmatched ?? []

  return (
    <div className="bg-slate-800/50 border border-indigo-500/30 rounded-lg p-4 space-y-4" dir="rtl">
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
        <input type="search" value={search} onChange={e => setSearch(e.target.value)}
          placeholder="جست‌وجو (IRC یا نام)…"
          className="flex-1 min-w-[12rem] bg-slate-900 border border-slate-600 rounded px-3 py-1 text-sm text-slate-100 placeholder:text-slate-500" />
        {isFetching && <span className="text-xs text-indigo-300">در حال بارگذاری…</span>}
      </div>

      {/* issue-type filter chips (AND across active) */}
      <div className="flex flex-wrap items-center gap-2">
        {ISSUE_TYPES.map(([key, label]) => {
          const active = activeChips.has(key)
          return (
            <button key={key} onClick={() => toggleChip(key)}
              className={`flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs border transition-colors ${
                active ? 'bg-indigo-600 border-indigo-500 text-white'
                       : 'bg-slate-900/40 border-slate-600 text-slate-300 hover:border-slate-400'}`}>
              {label}<span className="tabular-nums opacity-80">{fa(chipCounts[key])}</span>
            </button>)
        })}
        {activeChips.size > 0 && (
          <button onClick={() => setActiveChips(new Set())}
            className="text-xs text-slate-400 hover:text-slate-200 underline underline-offset-2">پاک‌کردن فیلترها</button>)}
      </div>

      {/* summary tiles */}
      <div className="flex flex-wrap gap-3">
        <div className="flex items-center gap-2 bg-slate-900/40 border border-slate-700 rounded-md px-3 py-2">
          <span className="text-xs text-slate-400">اقلام پرچم‌دار</span>
          <span className="text-lg font-semibold tabular-nums text-slate-100">{fa(flagged.length)}</span>
        </div>
        <div className="flex items-center gap-2 bg-slate-900/40 border border-slate-700 rounded-md px-3 py-2">
          <span className="text-xs text-slate-400">نامنطبق (در کاتالوگ یافت نشد)</span>
          <span className="text-lg font-semibold tabular-nums text-slate-100">{fa(data?.coverage.counts.unmatched ?? 0)}</span>
        </div>
      </div>

      {/* workbench: unified list (right/start) + detail drawer (left/end on lg) */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4 items-start">
        {/* unified flagged-drug list */}
        <div>
          {!data ? <p className="text-sm text-slate-500 py-4">در حال بارگذاری…</p>
            : shown.length === 0 ? (
              <Empty text={flagged.length === 0 ? 'هیچ قلم پرچم‌داری یافت نشد ✓' : 'موردی با این فیلترها یافت نشد.'} />
            ) : (
              <ScrollTable head={
                <tr><th className="px-3 py-2 text-right font-medium">IRC</th>
                    <th className="px-3 py-2 text-right font-medium">نام</th>
                    <th className="px-3 py-2 text-right font-medium">نشان‌ها</th>
                    <th className="px-3 py-2 text-left font-medium">اعلامی</th>
                    <th className="px-3 py-2 text-left font-medium">مرجع</th>
                    <th className="px-3 py-2 text-left font-medium">اختلاف٪</th></tr>}>
                {shown.map(f => (
                  <tr key={f.irc} onClick={() => setSelectedIrc(f.irc)}
                    className={`cursor-pointer transition-colors ${
                      selectedIrc === f.irc ? 'bg-indigo-500/15' : 'hover:bg-slate-700/25'}`}>
                    <td className="px-3 py-2 text-right tabular-nums font-mono text-slate-400 whitespace-nowrap">{f.irc}</td>
                    <td className="px-3 py-2 text-right text-slate-200">{f.name}</td>
                    <td className="px-3 py-2 text-right">
                      <span className="flex flex-wrap gap-1 justify-end">
                        {[...f.issues].map(iss => (
                          <span key={iss} className={`text-[10px] px-1.5 py-0.5 rounded border ${issueTone(iss, f.gap)}`}>
                            {ISSUE_LABEL[iss]}</span>))}
                      </span>
                    </td>
                    <td className="px-3 py-2 text-left tabular-nums text-slate-300 whitespace-nowrap">{fa(f.announced)}</td>
                    <td className="px-3 py-2 text-left tabular-nums text-slate-300 whitespace-nowrap">{fa(f.reference)}</td>
                    <td className={`px-3 py-2 text-left tabular-nums font-semibold whitespace-nowrap ${gapTone(f.gap)}`}>
                      {f.gap == null ? '—' : `${f.gap > 0 ? '+' : ''}${fa(f.gap)}٪`}
                    </td>
                  </tr>))}
              </ScrollTable>)}
        </div>

        {/* detail drawer */}
        <div className="lg:sticky lg:top-2">
          {selectedIrc
            ? <DrugDrawer irc={selectedIrc} insurer={insurer} onClose={() => setSelectedIrc(null)} />
            : <div className="text-sm text-slate-500 bg-slate-900/30 border border-dashed border-slate-700 rounded-md px-4 py-8 text-center">
                یک قلم را از فهرست انتخاب کنید تا کاتالوگ، پوشش بیمه، هم‌مولکول‌ها و تحلیل آن نمایش داده شود.
              </div>}
        </div>
      </div>

      {/* unmatched — no IRC, cannot join; separate section */}
      <section className="space-y-2">
        <div className="flex items-center gap-2">
          <h4 className="text-sm font-semibold text-slate-200">در کاتالوگ یافت نشد (نامنطبق)</h4>
          <Badge n={unmatched.length} />
        </div>
        {unmatched.length === 0
          ? <Empty text="همهٔ ردیف‌های دارونامه تطبیق داده شدند ✓" />
          : <ScrollTable head={
              <tr><th className="px-3 py-2 text-right font-medium">نام در دارونامه</th>
                  <th className="px-3 py-2 text-left font-medium">اطمینان</th></tr>}>
              {unmatched.map((it, i) => (
                <tr key={i} className="hover:bg-slate-700/20">
                  <td className="px-3 py-2 text-right text-slate-200">{String(it.row?.drug_name ?? it.row?.name ?? '—')}</td>
                  <td className="px-3 py-2 text-left tabular-nums text-slate-400">{fa(it.confidence)}</td>
                </tr>))}
            </ScrollTable>}
      </section>
    </div>
  )
}

// Per-drug detail drawer: catalog + all-insurer coverage + same-generic siblings + analysis.
function DrugDrawer({ irc, insurer, onClose }: { irc: string; insurer: string; onClose: () => void }) {
  const { data: detail, isFetching, isError } = useQuery<DrugDetail>({
    queryKey: ['incons-drug', irc, insurer],
    queryFn: () => pricingApi.inconsistencyDrug(irc, insurer).then(r => r.data),
  })

  const cat = detail?.catalog
  const selRef = detail?.coverage?.[insurer]?.reference_price ?? null
  const selStrength = cat?.strength ?? null
  // cross-strength smoking gun: sibling sharing this insurer's reference but a different strength
  const isCross = (s: DrugDetail['siblings'][number]) =>
    s.reference_price != null && selRef != null && s.reference_price === selRef
    && (s.strength ?? '') !== (selStrength ?? '')

  const insurerLabel = (k: string) => INSURERS.find(([v]) => v === k)?.[1] ?? k
  const catField = (label: string, val: unknown) => {
    const empty = val == null || val === ''
    return (
      <div className="flex justify-between gap-3 py-0.5">
        <span className="text-slate-500 shrink-0">{label}</span>
        <span className={`text-left ${empty ? 'text-amber-300' : 'text-slate-200'}`}>{empty ? '—' : String(val)}</span>
      </div>)
  }

  return (
    <div className="bg-slate-900/50 border border-slate-700 rounded-lg p-4 space-y-4">
      <div className="flex items-start justify-between gap-3">
        <div>
          <p className="text-sm font-semibold text-slate-100">{cat?.name_fa || detail?.irc || irc}</p>
          <p className="text-xs font-mono tabular-nums text-slate-500">{irc}</p>
        </div>
        <button onClick={onClose}
          className="text-xs text-slate-400 hover:text-slate-100 border border-slate-600 rounded px-2 py-1">بستن ✕</button>
      </div>

      {isFetching && !detail ? <p className="text-sm text-slate-500 py-4">در حال بارگذاری…</p>
        : isError ? <Empty text="دریافت جزئیات قلم ناموفق بود." />
        : !detail ? null : (
        <div className="space-y-4">
          {/* کاتالوگ NFI */}
          <section className="space-y-1.5">
            <h5 className="text-xs font-semibold text-slate-300 uppercase tracking-wide">کاتالوگ NFI</h5>
            {!cat ? <Empty text="این IRC در کاتالوگ NFI موجود نیست." />
              : <div className="grid grid-cols-1 sm:grid-cols-2 gap-x-6 text-sm bg-slate-800/40 rounded-md p-3">
                  {catField('نام لاتین', cat.name_en)}
                  {catField('ژنریک', cat.generic_name)}
                  {catField('قدرت', cat.strength)}
                  {catField('شکل دارویی', cat.dosage_form)}
                  {catField('برند', cat.brand_name)}
                  {catField('سازنده', cat.manufacturer)}
                  {catField('کشور', cat.country)}
                  {catField('ATC', cat.atc)}
                  {catField('قیمت اعلامی', cat.announced_price == null ? null : fa(cat.announced_price))}
                  {catField('تعداد در بسته', cat.package_count)}
                  {catField('GTIN', cat.gtin)}
                  {catField('منبع', cat.source)}
                </div>}
          </section>

          {/* پوشش بیمه — a row per insurer present */}
          <section className="space-y-1.5">
            <h5 className="text-xs font-semibold text-slate-300 uppercase tracking-wide">پوشش بیمه</h5>
            {Object.keys(detail.coverage).length === 0 ? <Empty text="هیچ ردیف پوششی برای این قلم ثبت نشده." />
              : <ScrollTable head={
                  <tr><th className="px-3 py-2 text-right font-medium">بیمه‌گر</th>
                      <th className="px-3 py-2 text-center font-medium">پوشش</th>
                      <th className="px-3 py-2 text-left font-medium">سهم٪</th>
                      <th className="px-3 py-2 text-left font-medium">مرجع</th>
                      <th className="px-3 py-2 text-left font-medium">سقف</th>
                      <th className="px-3 py-2 text-right font-medium">تطبیق</th></tr>}>
                  {Object.entries(detail.coverage).map(([ins, e]) => (
                    <tr key={ins} className={ins === insurer ? 'bg-indigo-500/10' : 'hover:bg-slate-700/20'}>
                      <td className="px-3 py-2 text-right text-slate-200">{insurerLabel(ins)}</td>
                      <td className="px-3 py-2 text-center">{e.covered ? '✓' : e.covered === false ? '✕' : '—'}</td>
                      <td className="px-3 py-2 text-left tabular-nums text-slate-300">{fa(e.share_pct)}</td>
                      <td className="px-3 py-2 text-left tabular-nums text-slate-300 whitespace-nowrap">{fa(e.reference_price)}</td>
                      <td className="px-3 py-2 text-left tabular-nums text-slate-300 whitespace-nowrap">{fa(e.ceiling)}</td>
                      <td className="px-3 py-2 text-right text-xs text-slate-400 whitespace-nowrap">
                        {e.match_method ?? '—'}{e.match_confidence != null ? ` · ${fa(e.match_confidence)}` : ''}</td>
                    </tr>))}
                </ScrollTable>}
          </section>

          {/* گروه هم‌مولکول — siblings; amber = same reference, different strength */}
          <section className="space-y-1.5">
            <h5 className="text-xs font-semibold text-slate-300 uppercase tracking-wide">گروه هم‌مولکول</h5>
            {detail.siblings.length === 0 ? <Empty text="هم‌مولکول دیگری یافت نشد." />
              : <ScrollTable head={
                  <tr><th className="px-3 py-2 text-right font-medium">نام</th>
                      <th className="px-3 py-2 text-right font-medium">قدرت</th>
                      <th className="px-3 py-2 text-left font-medium">قیمت اعلامی</th>
                      <th className="px-3 py-2 text-left font-medium">مرجع بیمه</th></tr>}>
                  {detail.siblings.map(s => {
                    const cross = isCross(s)
                    return (
                      <tr key={s.irc} className={cross ? 'bg-amber-500/15' : 'hover:bg-slate-700/20'}>
                        <td className="px-3 py-2 text-right text-slate-200">
                          {s.name_fa}{cross && <span className="mr-1 text-[10px] text-amber-300">◄ مرجع مشترک</span>}</td>
                        <td className={`px-3 py-2 text-right ${cross ? 'text-amber-300' : 'text-slate-300'}`}>{s.strength ?? '—'}</td>
                        <td className="px-3 py-2 text-left tabular-nums text-slate-300 whitespace-nowrap">{fa(s.announced_price)}</td>
                        <td className={`px-3 py-2 text-left tabular-nums whitespace-nowrap ${cross ? 'text-amber-300 font-semibold' : 'text-slate-300'}`}>
                          {fa(s.reference_price)}</td>
                      </tr>)
                  })}
                </ScrollTable>}
          </section>

          {/* تحلیل — root-cause hints */}
          <section className="space-y-1.5">
            <h5 className="text-xs font-semibold text-slate-300 uppercase tracking-wide">تحلیل</h5>
            {detail.analysis.length === 0
              ? <Empty text="نکتهٔ تحلیلی‌ای یافت نشد ✓" />
              : <ul className="space-y-1.5">
                  {detail.analysis.map((h, i) => (
                    <li key={i} className="text-sm text-amber-200 bg-amber-500/10 border border-amber-500/25 rounded-md px-3 py-2 leading-relaxed">
                      {h}</li>))}
                </ul>}
          </section>
        </div>)}
    </div>
  )
}

// ── هوش‌یار دارو — smart enrichment (worklist → research → review → approve) ───
interface Suggestion {
  id: string; key: string; raw_name: string; irc: string | null
  generic_name: string | null; brand_name: string | null; manufacturer: string | null
  country: string | null; dosage_form: string | null; strengths: string[] | null
  notes: string | null; sources: string[] | null; researched_by: string
  confidence: number | null; status: string
}
interface EnrichRunStatus {
  running: boolean; phase: string; total: number; done: number; saved: number
  skipped: number; failed: number; current: string; error: string | null
  elapsed_sec: number; recent: { name: string; result: string }[]
}
const REASON_FA: Record<string, string> = {
  unmatched: 'نامنطبق', low_confidence: 'اطمینان پایین', missing_details: 'جزئیات ناقص',
}

function EnrichmentPanel({ onMsg, onError }: {
  onMsg: (m: { kind: 'ok' | 'err'; text: string }) => void
  onError: (e: unknown, fallback: string) => void
}) {
  const qc = useQueryClient()
  const [limit, setLimit] = useState(50)
  const [sel, setSel] = useState<Set<string>>(new Set())
  const [busy, setBusy] = useState(false)

  const { data: work } = useQuery<{ total: number; counts: Record<string, number> }>({
    queryKey: ['enrich-worklist'],
    queryFn: () => pricingApi.enrichWorklist().then(r => r.data),
    refetchInterval: 30_000,
  })
  const { data: run } = useQuery<EnrichRunStatus>({
    queryKey: ['enrich-run-status'],
    queryFn: () => pricingApi.enrichRunStatus().then(r => r.data),
    refetchInterval: q => (q.state.data?.running ? 2_000 : 20_000),
  })
  const { data: sugg } = useQuery<{ count: number; suggestions: Suggestion[] }>({
    queryKey: ['enrich-suggestions'],
    queryFn: () => pricingApi.enrichSuggestions('suggested').then(r => r.data),
    refetchInterval: run?.running ? 5_000 : 30_000,
  })
  const rows = sugg?.suggestions || []

  const startRun = async () => {
    onMsg({ kind: 'ok', text: 'پژوهش آغاز شد…' })
    try {
      await pricingApi.enrichRun({ limit, min_confidence: 0.7 })
      qc.invalidateQueries({ queryKey: ['enrich-run-status'] })
    } catch (e) { onError(e, 'شروع پژوهش ناموفق بود.') }
  }
  const decide = async (ids: string[], approve: boolean) => {
    if (!ids.length) return
    setBusy(true)
    try {
      const { data } = await pricingApi.enrichDecide(ids, approve)
      onMsg({ kind: 'ok', text: `${fa(data.updated)} مورد ${approve ? 'تأیید' : 'رد'} شد.` })
      setSel(new Set())
      qc.invalidateQueries({ queryKey: ['enrich-suggestions'] })
      qc.invalidateQueries({ queryKey: ['enrich-worklist'] })
    } catch (e) { onError(e, 'ثبت تصمیم ناموفق بود.') }
    finally { setBusy(false) }
  }
  const exportRef = async () => {
    try {
      const { data } = await pricingApi.enrichExport()
      onMsg({ kind: 'ok', text: `${fa(data.exported)} ردیف تأییدشده به ${data.path} نوشته شد.` })
    } catch (e) { onError(e, 'برون‌سپاری ناموفق بود.') }
  }
  const importRef = async () => {
    try {
      const { data } = await pricingApi.enrichImport()
      onMsg({ kind: 'ok', text: `${fa(data.imported)} ردیف از مرجع بارگذاری شد.` })
      qc.invalidateQueries({ queryKey: ['enrich-suggestions'] })
    } catch (e) { onError(e, 'بارگذاری مرجع ناموفق بود.') }
  }
  const toggle = (id: string) => setSel(s => {
    const n = new Set(s); n.has(id) ? n.delete(id) : n.add(id); return n
  })

  return (
    <div className="bg-slate-800/50 border border-fuchsia-500/30 rounded-lg p-4 space-y-4" dir="rtl">
      <div className="flex items-center gap-2 flex-wrap">
        <h3 className="font-bold text-fuchsia-200">✨ هوش‌یار دارو — غنی‌سازی هوشمند</h3>
        <span className="text-[12px] text-slate-400">
          پژوهش وب برای اقلام نامنطبق و ناقص؛ نتایج «پیشنهادی» هستند و فقط پس از تأیید شما اعمال می‌شوند.
        </span>
      </div>

      {/* worklist summary */}
      <div className="flex flex-wrap items-center gap-2 text-[12px]">
        <span className="text-slate-300">فهرست کار: <b className="text-slate-100">{fa(work?.total)}</b> قلم</span>
        {Object.entries(work?.counts || {}).map(([reason, n]) => (
          <span key={reason} className="px-2 py-0.5 rounded-full bg-slate-700 border border-slate-600">
            {REASON_FA[reason] || reason}: {fa(n)}
          </span>
        ))}
      </div>

      {/* run controls */}
      <div className="flex flex-wrap items-center gap-2">
        <label className="text-[12px] text-slate-400">تعداد در این اجرا</label>
        <input type="number" min={1} max={500} value={limit}
          onChange={e => setLimit(Math.max(1, Math.min(500, Number(e.target.value) || 1)))}
          className="w-20 bg-slate-900 border border-slate-600 rounded px-2 py-1 text-sm tabular-nums" />
        <button onClick={startRun} disabled={run?.running}
          className="px-3 py-1.5 text-sm rounded-md bg-fuchsia-600 hover:bg-fuchsia-500 disabled:opacity-40">
          {run?.running ? 'در حال پژوهش…' : 'شروع پژوهش'}
        </button>
        <div className="mr-auto flex gap-2">
          <button onClick={exportRef} className="px-2.5 py-1 text-[12px] rounded bg-slate-700 hover:bg-slate-600">
            برون‌سپاری مرجع ⬇</button>
          <button onClick={importRef} className="px-2.5 py-1 text-[12px] rounded bg-slate-700 hover:bg-slate-600">
            بارگذاری مرجع ⬆</button>
        </div>
      </div>

      {/* live progress */}
      {run?.running && (
        <div className="bg-fuchsia-500/10 border border-fuchsia-500/40 rounded-lg p-3 text-[12px] font-mono space-y-1">
          <div className="flex flex-wrap gap-x-5">
            <span className="text-fuchsia-300">مرحله: {run.phase}</span>
            <span>پیشرفت: {fa(run.done)}/{fa(run.total)}</span>
            <span className="text-emerald-300">ثبت‌شده: {fa(run.saved)}</span>
            <span className="text-slate-400">ردشده: {fa(run.skipped)}</span>
            <span className="text-red-400">ناموفق: {fa(run.failed)}</span>
            <span>{run.elapsed_sec}s</span>
          </div>
          {run.current && <div className="text-slate-300 truncate">در حال بررسی: {run.current}</div>}
        </div>
      )}
      {run && !run.running && run.error &&
        <p className="text-[12px] text-red-400">خطای آخرین اجرا: {run.error}</p>}

      {/* review queue */}
      <div className="space-y-2">
        <div className="flex items-center gap-2">
          <p className="font-semibold text-sm">صف بازبینی ({fa(rows.length)})</p>
          {rows.length > 0 && <>
            <button onClick={() => setSel(new Set(rows.map(r => r.id)))}
              className="px-2 py-0.5 text-[12px] rounded bg-slate-700 hover:bg-slate-600">انتخاب همه</button>
            <button onClick={() => decide([...sel], true)} disabled={busy || sel.size === 0}
              className="px-2 py-0.5 text-[12px] rounded bg-emerald-700 hover:bg-emerald-600 disabled:opacity-40">
              تأیید انتخاب‌شده‌ها ({fa(sel.size)})</button>
            <button onClick={() => decide([...sel], false)} disabled={busy || sel.size === 0}
              className="px-2 py-0.5 text-[12px] rounded bg-red-800 hover:bg-red-700 disabled:opacity-40">
              رد انتخاب‌شده‌ها</button>
          </>}
        </div>
        {rows.length === 0
          ? <Empty text="هیچ پیشنهاد در انتظار بازبینی نیست." />
          : <div className="space-y-1.5">
              {rows.map(s => (
                <div key={s.id} className="flex flex-wrap items-start gap-x-3 gap-y-1 text-[12px] border border-slate-700/60 rounded-md p-2">
                  <input type="checkbox" checked={sel.has(s.id)} onChange={() => toggle(s.id)} className="mt-1" />
                  <div className="flex-1 min-w-[16rem] space-y-0.5">
                    <div className="font-semibold text-slate-100">{s.raw_name}</div>
                    <div className="text-slate-300">
                      {[s.generic_name, s.brand_name, s.manufacturer, s.country,
                        s.dosage_form, (s.strengths || []).join(' / ')]
                        .filter(Boolean).join(' · ') || '—'}
                    </div>
                    <div className="flex flex-wrap items-center gap-2 text-[11px] text-slate-400">
                      <span className="px-1.5 py-0.5 rounded bg-slate-700">{s.researched_by}</span>
                      {s.confidence != null &&
                        <span>اطمینان: {new Intl.NumberFormat('fa-IR', { style: 'percent' }).format(s.confidence)}</span>}
                      {(s.sources || []).slice(0, 3).map((u, i) => (
                        <a key={i} href={u} target="_blank" rel="noreferrer"
                          className="text-indigo-300 hover:underline truncate max-w-[14rem]">منبع {fa(i + 1)}↗</a>))}
                    </div>
                  </div>
                  <div className="flex gap-1.5">
                    <button onClick={() => decide([s.id], true)} disabled={busy}
                      className="px-2 py-0.5 rounded bg-emerald-700 hover:bg-emerald-600 disabled:opacity-40">تأیید</button>
                    <button onClick={() => decide([s.id], false)} disabled={busy}
                      className="px-2 py-0.5 rounded bg-red-800 hover:bg-red-700 disabled:opacity-40">رد</button>
                  </div>
                </div>
              ))}
            </div>}
      </div>
    </div>
  )
}
