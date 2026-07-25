/**
 * CoverageAdmin — دارونامه sources, harvest & review. Per-insurer source cards
 * (probe → detect format → save column overrides), background harvest behind the
 * global proxy lock, staged runs with a diff vs live coverage, and preview→اعمال.
 * The one-shot upload card (instant apply) also lives here, moved from DrugCatalogAdmin.
 */
import { useEffect, useMemo, useRef, useState, type ReactNode } from 'react'
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

// Tabs mirror the real workflow order: configure → fetch → review → fix → price.
const TABS = [
  { id: 'sources', label: 'منابع' },
  { id: 'tamin',   label: 'برداشت تأمین' },
  { id: 'runs',    label: 'اجراها' },
  { id: 'issues',  label: 'ناسازگاری‌ها' },
  { id: 'enrich',  label: '✨ غنی‌سازی' },
  { id: 'catalog', label: '🗂 کاتالوگ NFI' },
  { id: 'prices',  label: '📉 قیمت‌ها' },
  { id: 'decisions', label: '⚖ تصمیم‌ها' },
] as const
type TabId = typeof TABS[number]['id']

export default function CoverageAdmin() {
  const qc = useQueryClient()
  const [msg, setMsg] = useState<{ kind: 'ok' | 'err'; text: string } | null>(null)
  const [probe, setProbe] = useState<{ sourceId: string; data: any } | null>(null)
  const [openRun, setOpenRun] = useState<string | null>(null)
  const [tab, setTab] = useState<TabId>('sources')

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

  const runList = runs?.runs || []
  const pendingReview = runList.filter(r => r.status === 'parsed').length
  const lastRun = runList[0]

  return (
    <div className="p-4 pb-10 space-y-4 text-slate-100" dir="rtl">
      {/* ── header: title + at-a-glance state (no scrolling to learn status) ── */}
      <header className="space-y-3">
        <div className="flex items-baseline gap-3 flex-wrap">
          <h2 className="text-lg font-bold">پوشش بیمه</h2>
          <span className="text-[12px] text-slate-500">دارونامهٔ بیمه‌گرها — برداشت، بازبینی و اعمال</span>
        </div>
        <div className="grid grid-cols-2 md:grid-cols-4 gap-2">
          <Stat label="منابع" value={fa((sources?.sources || []).length)}
                hint={locked ? 'قفل برداشت فعال' : 'آماده'} tone={locked ? 'amber' : 'slate'} />
          <Stat label="اجرای در انتظار تأیید" value={fa(pendingReview)}
                hint={pendingReview ? 'نیازمند بازبینی' : 'موردی نیست'}
                tone={pendingReview ? 'amber' : 'emerald'} />
          <Stat label="آخرین اجرا" value={lastRun?.insurer || '—'}
                hint={lastRun?.finished_at
                  ? new Date(lastRun.finished_at).toLocaleDateString('fa-IR') : '—'} />
          <Stat label="وضعیت برداشت" value={hs?.running ? 'در حال اجرا' : 'بی‌کار'}
                hint={hs?.running ? `${hs.insurer} · ${hs.phase}` : 'آمادهٔ شروع'}
                tone={hs?.running ? 'indigo' : 'slate'} />
        </div>
      </header>

      {/* ── one concern per tab: the page no longer stacks every panel at once ── */}
      <nav className="flex gap-1 border-b border-slate-700 overflow-x-auto" role="tablist">
        {TABS.map(t => (
          <button key={t.id} role="tab" aria-selected={tab === t.id}
            onClick={() => setTab(t.id)}
            className={`px-3 py-2 text-sm whitespace-nowrap border-b-2 -mb-px transition-colors ${
              tab === t.id
                ? 'border-indigo-400 text-indigo-200'
                : 'border-transparent text-slate-400 hover:text-slate-200'}`}>
            {t.label}
            {t.id === 'runs' && pendingReview > 0 && (
              <span className="mr-1.5 px-1.5 py-0.5 rounded-full bg-amber-500/20 text-amber-300 text-[10px]">
                {fa(pendingReview)}
              </span>)}
          </button>
        ))}
      </nav>

      {msg && <p className={`text-sm ${msg.kind === 'ok' ? 'text-emerald-400' : 'text-red-400'}`}>{msg.text}</p>}

      {hs?.running && (
        <div className="bg-indigo-500/10 border border-indigo-500/40 rounded-lg p-3 text-[12px] font-mono flex flex-wrap gap-x-5">
          <span className="text-indigo-300">در حال برداشت: {hs.insurer}</span>
          <span>مرحله: {hs.phase}</span><span>صفحات: {fa(hs.pages)}</span>
          <span>ردیف‌ها: {fa(hs.rows)}</span>
          {hs.error && <span className="text-red-400">{hs.error}</span>}
        </div>
      )}

      {tab === 'sources' && (
        <div className="space-y-3">
          <div className="grid md:grid-cols-2 gap-3">
            {(sources?.sources || []).map(s => (
              <SourceCard key={s.id} s={s} locked={locked} lockHolder={hs?.lock_holder ?? s.lock_holder}
                          onProbe={() => doProbe(s)} onHarvest={() => doHarvest(s)}
                          onSave={patch => saveSource(s, patch)} />
            ))}
          </div>
          {probe && (
            <ProbePanel data={probe.data} onClose={() => setProbe(null)}
              onSaveOverrides={(ov) => {
                const s = sources?.sources.find(x => x.id === probe.sourceId)
                if (s) saveSource(s, { settings: { ...s.settings, column_overrides: ov },
                                       strategy: probe.data.detected_strategy })
                setProbe(null)
              }} />
          )}
          <UploadCard onMsg={setMsg} />
        </div>
      )}

      {tab === 'tamin' && <TaminHarvestPanel onMsg={setMsg} onError={err} />}

      {tab === 'runs' && (
        <div className="bg-slate-800/50 border border-slate-700 rounded-lg p-4 space-y-2">
          <p className="font-semibold text-sm">اجراهای برداشت</p>
          {runList.length === 0 && <p className="text-[12px] text-slate-500">هنوز اجرایی ثبت نشده.</p>}
          {runList.map(r => (
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
      )}

      {tab === 'issues' && (<div className="space-y-5">
        <TriageBoard onError={err} />
        <details className="bg-slate-800/30 border border-slate-700 rounded-lg">
          <summary className="px-4 py-2 text-sm cursor-pointer text-slate-300">
            🔎 فهرست تفصیلی اقلام (برای بررسی موردی)
          </summary>
          <div className="p-2"><InconsistenciesPanel onError={err} /></div>
        </details>
      </div>)}
      {tab === 'enrich' && <EnrichmentPanel onMsg={setMsg} onError={err} />}
      {tab === 'catalog' && <CatalogEditorPanel onMsg={setMsg} onError={err} />}
      {tab === 'decisions' && <DecisionsPanel onMsg={setMsg} onError={err} />}
      {tab === 'prices' && <PriceHistoryPanel onMsg={setMsg} onError={err} />}
    </div>
  )
}

/** Compact KPI tile — the header's job is to answer "what needs me?" at a glance. */
function Stat({ label, value, hint, tone = 'slate' }: {
  label: string; value: React.ReactNode; hint?: string
  tone?: 'slate' | 'amber' | 'emerald' | 'indigo'
}) {
  const ring: Record<string, string> = {
    slate: 'border-slate-700', amber: 'border-amber-500/40',
    emerald: 'border-emerald-500/30', indigo: 'border-indigo-500/40',
  }
  return (
    <div className={`bg-slate-800/40 border ${ring[tone]} rounded-lg px-3 py-2`}>
      <p className="text-[11px] text-slate-500">{label}</p>
      <p className="text-sm font-semibold text-slate-100 truncate">{value}</p>
      {hint && <p className="text-[11px] text-slate-500 truncate">{hint}</p>}
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
  const [reasons, setReasons] = useState<Record<number, { code?: string; note?: string }>>({})
  const [bulkMsg, setBulkMsg] = useState('')
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
      if (approve) {
        // Only reasons for items NOT accepted (a coded refusal is a training label)
        const reject_reasons: Record<string, { code?: string; note?: string }> = {}
        for (const [id, r] of Object.entries(reasons)) {
          if (!accepted.has(Number(id)) && (r.code || r.note)) reject_reasons[id] = r
        }
        await pricingApi.coverageApprove(runId,
          { remove_missing: removeMissing, accepted_review_ids: [...accepted],
            reject_reasons })
      }
      else await pricingApi.coverageReject(runId)
      onDone()
    } catch (e) { onError(e, 'تصمیم اعمال نشد.') } finally { setBusy(false) }
  }
  const proposePrices = async () => {
    setBusy(true)
    try {
      const { data } = await pricingApi.coverageProposePrices(runId,
        { min_confidence: 0.85, min_pct: 25 })
      setBulkMsg(`${fa(data.proposals_created)} پیشنهاد قیمت از ${fa(data.qualified)} تطبیق مطمئن ساخته شد `
        + `(↑${fa(data.by_kind?.increase || 0)} / ↓${fa(data.by_kind?.decrease || 0)}) — در «پیشنهادهای قیمت» بازبینی کنید.`)
    } catch (e) { onError(e, 'ساخت پیشنهاد قیمت ناموفق بود.') } finally { setBusy(false) }
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
      {(run.groups?.bulk_candidates || []).length > 0 && (
        <details className="border border-cyan-600/30 rounded p-2">
          <summary className="cursor-pointer text-cyan-300">
            گروه‌بندی مادهٔ مؤثره — {fa(run.stats?.ingredient_groups)} گروه ·
            {' '}{fa(run.groups.bulk_candidates.length)} نامزد «مادهٔ اولیه»
          </summary>
          <p className="text-[11px] text-slate-500 mt-1">
            این‌ها ماده‌های اولیهٔ داروسازی به‌نظر می‌رسند (بدون شکل/قدرت، کنارِ اقلام نهاییِ همان ماده).
            تأیید ⇒ به‌عنوان «فله» ثبت و از تطبیق دارو کنار گذاشته می‌شوند.
          </p>
          <button onClick={async () => {
            setBusy(true)
            try {
              const { data } = await pricingApi.enrichMarkBulk(run.groups.bulk_candidates)
              setBulkMsg(`${fa(data.marked)} مورد به‌عنوان «مادهٔ اولیه» ثبت شد (${fa(data.skipped)} رد شد).`)
            } catch (e) { onError(e, 'ثبت فله ناموفق بود.') } finally { setBusy(false) }
          }} disabled={busy}
            className="mt-1 mb-1 px-2 py-0.5 text-[11px] rounded bg-cyan-800 hover:bg-cyan-700 disabled:opacity-40">
            تأیید همه به‌عنوان «مادهٔ اولیه» ({fa(run.groups.bulk_candidates.length)})
          </button>
          {bulkMsg && <span className="text-[11px] text-emerald-400 mr-2">{bulkMsg}</span>}
          <div className="max-h-32 overflow-y-auto mt-1 space-y-0.5 font-mono text-[11px] text-slate-400">
            {run.groups.bulk_candidates.map((n: string, i: number) => (
              <div key={i} className="flex items-center gap-2">
                <button onClick={async () => {
                  try { await pricingApi.enrichMarkBulk([n]) } catch (e) { onError(e, 'ثبت فله ناموفق بود.') }
                }} className="text-cyan-400 hover:text-cyan-200" title="ثبت این مورد به‌عنوان مادهٔ اولیه">فله ✓</button>
                <span>{n}</span>
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
          {[...run.review].sort((a: any, b: any) =>
            Math.abs(a.fs ?? 999) - Math.abs(b.fs ?? 999)   // active learning: boundary cases first
          ).map((item: any) => (
            <div key={item.id} className="flex flex-wrap items-center gap-2 font-mono text-slate-400">
              <label className="flex items-center gap-2">
                <input type="checkbox" checked={accepted.has(item.id)}
                  onChange={e => { const s = new Set(accepted); e.target.checked ? s.add(item.id) : s.delete(item.id); setAccepted(s) }} />
                {item.name} ← «{String(item.row?.drug_name ?? '')}» (اطمینان {item.confidence}
                {item.fs != null && <span title="امتیاز هوش تطبیق">{' '}· FS {item.fs}</span>})
              </label>
              {!accepted.has(item.id) && (
                <span className="flex items-center gap-1">
                  <select value={reasons[item.id]?.code || ''}
                    onChange={e => setReasons(r => ({ ...r, [item.id]: { ...r[item.id], code: e.target.value || undefined } }))}
                    className="bg-slate-900 border border-slate-700 rounded px-1 py-0.5 text-[11px]">
                    <option value="">دلیل رد…</option>
                    <option value="wrong_product">داروی دیگر</option>
                    <option value="wrong_strength">قدرت اشتباه</option>
                    <option value="wrong_form">شکل اشتباه</option>
                    <option value="wrong_brand">برند اشتباه</option>
                    <option value="wrong_pack">بستهٔ اشتباه</option>
                    <option value="price_implausible">قیمت نامعقول</option>
                    <option value="other">سایر</option>
                  </select>
                  <input type="text" placeholder="توضیح (اختیاری)"
                    value={reasons[item.id]?.note || ''}
                    onChange={e => setReasons(r => ({ ...r, [item.id]: { ...r[item.id], note: e.target.value || undefined } }))}
                    className="bg-slate-900 border border-slate-700 rounded px-1.5 py-0.5 text-[11px] w-40" />
                </span>
              )}
            </div>))}
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
        <button onClick={proposePrices} disabled={busy}
          title="قیمت‌های جاری این بیمه‌گر را برای تطبیق‌های مطمئن به‌عنوان پیشنهاد به‌روزرسانی قیمت بساز (بازبینی جداگانه)"
          className="px-4 py-1.5 bg-cyan-700 hover:bg-cyan-600 rounded disabled:opacity-50">💰 به‌روزرسانی قیمت از این اجرا</button>
        <span className="text-[11px] text-slate-500">موارد تأییدشده فقط همراه «اعمال اجرا» اعمال می‌شوند.</span>
      </div>
    </div>
  )
}

const UPLOAD_PHASE_FA: Record<string, string> = {
  starting: 'در حال آماده‌سازی…', fetching: 'در حال دریافت…',
  linking: 'در حال تطبیق با کاتالوگ…', diffing: 'در حال مقایسه با پوشش فعلی…',
  saving: 'در حال ذخیرهٔ اجرا…', done: 'انجام شد', failed: 'ناموفق',
}

function UploadCard({ onMsg }: { onMsg: (m: { kind: 'ok' | 'err'; text: string }) => void }) {
  const qc = useQueryClient()
  const covRef = useRef<HTMLInputElement>(null)
  const [covInsurer, setCovInsurer] = useState('tamin')
  const [files, setFiles] = useState<File[]>([])
  const [busy, setBusy] = useState(false)
  const [tracking, setTracking] = useState(false)   // our upload is the running job
  const [fileStats, setFileStats] = useState<{ file: string; rows: number }[] | null>(null)

  // shared harvest state — the staged upload runs through the same machinery
  const { data: hs } = useQuery<{ running: boolean; phase: string; rows: number;
                                  insurer: string; error: string | null; run_id: string | null }>({
    queryKey: ['coverage-harvest-status'],
    queryFn: () => pricingApi.coverageHarvestStatus().then(r => r.data),
    refetchInterval: q => (q.state.data?.running ? 2_000 : 15_000),
    enabled: tracking,
  })
  useEffect(() => {
    if (!tracking || !hs || hs.running) return
    setTracking(false)
    qc.invalidateQueries({ queryKey: ['coverage-runs'] })
    if (hs.phase === 'done') {
      onMsg({ kind: 'ok', text: 'پردازش کامل شد — اجرای جدید در تب «اجراها» آمادهٔ بازبینی است.' })
    } else if (hs.error) {
      onMsg({ kind: 'err', text: `پردازش ناموفق: ${hs.error}` })
    }
  }, [tracking, hs, qc, onMsg])

  const start = async () => {
    if (!files.length) return
    setBusy(true); setFileStats(null)
    try {
      const { data } = await pricingApi.uploadCoverageRun(files, covInsurer)
      setFileStats(data.files)
      setTracking(true)
      qc.invalidateQueries({ queryKey: ['coverage-harvest-status'] })
      onMsg({ kind: 'ok', text: `${fa(data.merged_rows)} ردیف استخراج و ادغام شد — پردازش در پس‌زمینه آغاز شد.` })
      setFiles([]); if (covRef.current) covRef.current.value = ''
    } catch (e: unknown) {
      onMsg({ kind: 'err', text: apiErrorText(e, 'بارگذاری دارونامه ناموفق بود.') })
    } finally { setBusy(false) }
  }

  const working = busy || (tracking && !!hs?.running)
  return (
    <div className="bg-slate-800/50 border border-slate-700 rounded-lg p-4 space-y-2">
      <p className="font-semibold text-sm">بارگذاری دستی دارونامه (اجرای مرحله‌ای)</p>
      <p className="text-[11px] text-slate-500">
        Excel/CSV/JSON یا صفحه HTML ذخیره‌شده — چند فایل از یک دارونامه (مثلاً ‎.json و ‎.csv تأمین)
        با کد دارو ادغام می‌شوند. نتیجه به‌صورت اجرای قابل بازبینی در تب «اجراها» ظاهر می‌شود.
      </p>
      <div className="flex flex-wrap items-center gap-3 text-sm">
        <select value={covInsurer} onChange={e => setCovInsurer(e.target.value)} disabled={working}
          className="bg-slate-900 border border-slate-600 rounded px-2 py-1">
          <option value="tamin">تأمین اجتماعی</option>
          <option value="salamat">بیمه سلامت</option>
          <option value="armed_forces">نیروهای مسلح</option>
        </select>
        <input ref={covRef} type="file" multiple accept=".xlsx,.xls,.csv,.tsv,.html,.htm,.json"
          className="text-sm text-slate-300" disabled={working}
          onChange={e => setFiles(Array.from(e.target.files || []))} />
        <button onClick={start} disabled={working || !files.length}
          className="px-4 py-1.5 bg-emerald-600 hover:bg-emerald-500 rounded disabled:opacity-50">
          🚀 بارگذاری و پردازش
        </button>
      </div>
      {files.length > 0 && !working && (
        <ul className="text-[11px] text-slate-400 space-y-0.5">
          {files.map(f => <li key={f.name}>📄 {f.name} — {fa(Math.max(1, Math.round(f.size / 1024)))} KB</li>)}
        </ul>)}
      {working && (
        <div className="flex items-center gap-2 text-xs text-cyan-300">
          <span className="inline-block w-3 h-3 border-2 border-cyan-400 border-t-transparent rounded-full animate-spin" />
          <span>{busy ? 'در حال بارگذاری فایل(ها)…'
                      : (UPLOAD_PHASE_FA[hs?.phase || ''] || hs?.phase)}{' '}
            {!busy && hs?.rows ? `· ${fa(hs.rows)} ردیف` : ''}</span>
        </div>)}
      {fileStats && (
        <div className="text-[11px] text-slate-400">
          {fileStats.map(s => <span key={s.file} className="ml-3">📄 {s.file}: {fa(s.rows)} ردیف</span>)}
        </div>)}
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

// Drug-anchored issue types (order = chip order). label + list badge tone.
const ISSUE_TYPES = [
  ['price', 'مغایرت قیمت'], ['review', 'نیازمند بازبینی'], ['unmatched', 'نامنطبق'],
  ['no_price', 'بدون قیمت'],
  ['no_generic', 'بدون ژنریک'], ['no_country', 'بدون کشور'], ['no_atc', 'بدون ATC'],
] as const
type IssueType = (typeof ISSUE_TYPES)[number][0]
const ISSUE_LABEL = Object.fromEntries(ISSUE_TYPES) as Record<IssueType, string>

interface FlaggedDrug {
  irc: string; name: string; issues: Set<IssueType>
  /** the insurer formulary's own row text — what the source actually printed */
  formulary: string | null
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

// ── مغایرت داخلی NFI: spliced legacy pages (product block ≠ monograph) ───────
interface IntegritySuspect {
  irc: string; name_fa: string | null; brand_name: string | null
  manufacturer: string | null; gtin: string | null; announced_price: number | null
  current: { generic_name: string | null; dosage_form: string | null;
             strength: string | null; atc: string | null }
  reasons: string[]
  proposal: { generic_name: string | null; dosage_form: string | null;
              strength: string | null; atc: string | null }
  donor_irc: string | null
  price_flag: string | null
}

function NfiIntegrityCard({ onError }: { onError: (e: unknown, f: string) => void }) {
  const qc = useQueryClient()
  const [open, setOpen] = useState(false)
  const [sel, setSel] = useState<Set<string>>(new Set())
  const [busy, setBusy] = useState(false)
  const { data } = useQuery<{ suspects: IntegritySuspect[];
                              counts: { checked: number; suspect: number; with_donor: number } }>({
    queryKey: ['nfi-integrity'],
    queryFn: () => pricingApi.catalogIntegrity().then(r => r.data),
    staleTime: 5 * 60_000,
  })
  const suspects = data?.suspects ?? []
  const toggle = (irc: string) => setSel(s => {
    const n = new Set(s); n.has(irc) ? n.delete(irc) : n.add(irc); return n
  })
  const apply = async (ircs: string[]) => {
    if (!ircs.length) return
    setBusy(true)
    try {
      const { data: res } = await pricingApi.catalogIntegrityApply(ircs)
      qc.invalidateQueries({ queryKey: ['nfi-integrity'] })
      qc.invalidateQueries({ queryKey: ['inconsistencies'] })
      setSel(new Set())
      alert(`اصلاح شد: ${fa(res.repaired)} قلم — اصلاح‌ها به‌صورت override دائمی ثبت شدند.`)
    } catch (e) { onError(e, 'اعمال اصلاح ناموفق بود.') } finally { setBusy(false) }
  }
  if (!suspects.length) return null
  return (
    <div className="bg-rose-950/30 border border-rose-500/40 rounded-lg p-3 space-y-2">
      <button onClick={() => setOpen(o => !o)} className="w-full flex items-center justify-between">
        <span className="font-semibold text-sm text-rose-200">
          🧬 مغایرت داخلی NFI — صفحات دوپاره ({fa(suspects.length)} قلم مشکوک از {fa(data!.counts.checked)})
        </span>
        <span className="text-rose-300 text-xs">{open ? '▲ بستن' : '▼ نمایش'}</span>
      </button>
      {open && (<>
        <p className="text-[11px] text-rose-200/70 leading-5">
          صفحات قدیمی سایت NFI گاهی مونوگرافِ دارویی دیگر را نشان می‌دهند (شناسهٔ ژنریکِ بازاستفاده‌شده) —
          نام/قیمت/تولیدکننده متعلق به خود قلم است ولی ژنریک/شکل/ATC متعلق به داروی دیگری.
          پیشنهادِ اصلاح از روی همتای سالمِ همان محصول (🎯) یا خودِ نام برند ساخته شده است.
          اصلاح تأییدشده به‌صورت override دائمی ثبت می‌شود و خزش بعدی نمی‌تواند آن را دوباره خراب کند.
        </p>
        <div className="flex items-center gap-2">
          <button disabled={busy} onClick={() => setSel(new Set(suspects.map(s => s.irc)))}
            className="px-2 py-1 text-xs bg-slate-700 hover:bg-slate-600 rounded">انتخاب همه</button>
          <button disabled={busy} onClick={() => setSel(new Set(suspects.filter(s => s.donor_irc).map(s => s.irc)))}
            className="px-2 py-1 text-xs bg-slate-700 hover:bg-slate-600 rounded"
            title="فقط مواردی که همتای سالم همان محصول پیدا شده">🎯 انتخاب موارد با همتا ({fa(data!.counts.with_donor)})</button>
          <button disabled={busy || !sel.size} onClick={() => apply([...sel])}
            className="px-3 py-1 text-xs bg-emerald-600 hover:bg-emerald-500 rounded disabled:opacity-50">
            ✔ اصلاح {fa(sel.size)} مورد انتخاب‌شده</button>
        </div>
        <div className="overflow-x-auto max-h-96 overflow-y-auto">
          <table className="w-full text-[11px]">
            <thead className="sticky top-0 bg-slate-900">
              <tr className="text-slate-400 text-right">
                <th className="p-1.5"></th><th className="p-1.5">برند (بلوک محصول)</th>
                <th className="p-1.5">مونوگراف فعلی (خراب)</th>
                <th className="p-1.5">پیشنهاد اصلاح</th><th className="p-1.5">منبع</th>
              </tr>
            </thead>
            <tbody>
              {suspects.map(s => (
                <tr key={s.irc} className="border-t border-slate-700/60 align-top">
                  <td className="p-1.5">
                    <input type="checkbox" checked={sel.has(s.irc)} onChange={() => toggle(s.irc)} />
                  </td>
                  <td className="p-1.5">
                    <div className="text-slate-100">{s.brand_name || s.name_fa}</div>
                    <div className="text-slate-500">{s.manufacturer} · {s.irc}
                      {s.announced_price != null && <> · {fa(s.announced_price)} ریال</>}</div>
                    {s.price_flag && <div className="text-amber-400">⚠ {s.price_flag}</div>}
                  </td>
                  <td className="p-1.5 text-rose-300">
                    {s.current.generic_name} · {s.current.dosage_form}
                    {s.current.atc && <> · {s.current.atc}</>}
                  </td>
                  <td className="p-1.5 text-emerald-300">
                    {s.proposal.generic_name} · {s.proposal.dosage_form || '—'}
                    {s.proposal.strength && <> · {s.proposal.strength}</>}
                    {s.proposal.atc && <> · {s.proposal.atc}</>}
                  </td>
                  <td className="p-1.5 text-slate-400">
                    {s.donor_irc ? <span title={`IRC همتا: ${s.donor_irc}`}>🎯 همتای سالم</span> : '📛 از نام برند'}
                  </td>
                </tr>))}
            </tbody>
          </table>
        </div>
      </>)}
    </div>
  )
}

// ── triage board: root causes, not rows ──────────────────────────────────────
interface Cause {
  cause: string; count: number; lane: string; lane_fa: string; title: string
  why: string; action: string; route: string; closed: boolean
  disposition: string | null; reason: string | null; decided_at: string | null
}
interface Board {
  causes: Cause[]
  totals: { open: number; acknowledged: number; all: number; by_lane: Record<string, number> }
  lanes: Record<string, string>
}

const LANE_STYLE: Record<string, string> = {
  auto:      'border-emerald-500/40 bg-emerald-500/5',
  bulk:      'border-cyan-500/40 bg-cyan-500/5',
  research:  'border-violet-500/40 bg-violet-500/5',
  judgement: 'border-amber-500/40 bg-amber-500/5',
  expected:  'border-slate-500/40 bg-slate-500/5',
  blocked:   'border-rose-500/40 bg-rose-500/5',
}
const ROUTE_HINT: Record<string, string> = {
  nfi_integrity: 'کاتالوگ NFI → مغایرت داخلی',
  run_review: 'اجراها → بازبینی اجرا',
  enrichment: '✨ غنی‌سازی',
  nfi_harvest: 'کاتالوگ NFI → شروع برداشت (نیازمند پروکسی)',
  price_review: '📉 قیمت‌ها',
  acknowledge: 'همین‌جا — پذیرش گروهی',
}

function TriageBoard({ onError }: { onError: (e: unknown, f: string) => void }) {
  const qc = useQueryClient()
  const [busy, setBusy] = useState<string | null>(null)
  const { data, isFetching } = useQuery<Board>({
    queryKey: ['issues-board'],
    queryFn: () => pricingApi.issuesBoard().then(r => r.data),
    refetchInterval: 60_000,
  })
  const rule = async (cause: string, disposition: string, reason?: string) => {
    setBusy(cause)
    try {
      await pricingApi.issuesRuling({ cause, disposition, reason })
      qc.invalidateQueries({ queryKey: ['issues-board'] })
    } catch (e) { onError(e, 'ثبت تصمیم ناموفق بود.') } finally { setBusy(null) }
  }
  const reopen = async (cause: string) => {
    setBusy(cause)
    try {
      await pricingApi.issuesRulingClear(cause)
      qc.invalidateQueries({ queryKey: ['issues-board'] })
    } catch (e) { onError(e, 'بازگشایی ناموفق بود.') } finally { setBusy(null) }
  }
  const t = data?.totals
  const pct = t && t.all ? Math.round(100 * t.acknowledged / t.all) : 0

  return (
    <div className="space-y-4">
      {/* burn-down header: the number must be able to go DOWN */}
      <div className="bg-slate-800/50 border border-slate-700 rounded-lg p-4 space-y-3">
        <div className="flex flex-wrap items-baseline gap-x-6 gap-y-1">
          <span className="text-sm font-semibold">تابلوی مدیریت ناسازگاری‌ها</span>
          <span className="text-2xl font-mono text-amber-300">{fa(t?.open ?? 0)}</span>
          <span className="text-[11px] text-slate-400">باز</span>
          <span className="text-lg font-mono text-emerald-300">{fa(t?.acknowledged ?? 0)}</span>
          <span className="text-[11px] text-slate-400">تصمیم‌گرفته ({fa(pct)}٪)</span>
          {isFetching && <span className="text-[11px] text-cyan-300">در حال محاسبه…</span>}
        </div>
        <div className="h-2 bg-slate-700 rounded-full overflow-hidden">
          <div className="h-full bg-emerald-500 transition-all" style={{ width: `${pct}%` }} />
        </div>
        <p className="text-[11px] text-slate-500">
          ناسازگاری‌ها بر پایهٔ «علت ریشه‌ای» گروه‌بندی شده‌اند، نه ردیف‌به‌ردیف: یک تصمیم،
          کل گروه را می‌بندد و در اجراهای بعدی دیگر تکرار نمی‌شود.
        </p>
        <div className="flex flex-wrap gap-2 text-[11px]">
          {Object.entries(t?.by_lane || {}).sort((a, b) => b[1] - a[1]).map(([lane, n]) => (
            <span key={lane} className={`px-2 py-0.5 rounded border ${LANE_STYLE[lane] || ''}`}>
              {data?.lanes[lane]}: {fa(n)}
            </span>))}
        </div>
      </div>

      {/* one card per root cause */}
      <div className="grid gap-3 lg:grid-cols-2">
        {(data?.causes || []).filter(c => c.count > 0 || c.closed).map(c => (
          <div key={c.cause}
            className={`rounded-lg border p-3 space-y-2 ${LANE_STYLE[c.lane] || 'border-slate-700'} ${c.closed ? 'opacity-60' : ''}`}>
            <div className="flex items-start justify-between gap-2">
              <div>
                <div className="text-sm font-semibold">{c.title}</div>
                <div className="text-[11px] text-slate-400">{c.lane_fa}</div>
              </div>
              <div className="text-xl font-mono shrink-0">{fa(c.count)}</div>
            </div>
            <p className="text-[11px] text-slate-400 leading-relaxed">{c.why}</p>
            <p className="text-[11px] text-cyan-300 leading-relaxed">◆ {c.action}</p>
            <p className="text-[10px] text-slate-500">مقصد: {ROUTE_HINT[c.route] || c.route}</p>
            {c.closed ? (
              <div className="flex items-center gap-2 text-[11px]">
                <span className="text-emerald-300">
                  ✓ {c.disposition === 'accepted' ? 'پذیرفته‌شده'
                    : c.disposition === 'wont_fix' ? 'رفع نمی‌شود'
                    : c.disposition === 'resolved' ? 'رفع‌شده' : 'به تعویق'}
                  {c.reason ? ` — ${c.reason}` : ''}
                </span>
                <button onClick={() => reopen(c.cause)} disabled={busy === c.cause}
                  className="px-2 py-0.5 bg-slate-700 hover:bg-slate-600 rounded disabled:opacity-50">
                  بازگشایی</button>
              </div>
            ) : (
              <div className="flex flex-wrap gap-2 text-[11px]">
                <button onClick={() => rule(c.cause, 'accepted', 'طبیعی و مورد انتظار')}
                  disabled={busy === c.cause}
                  className="px-2 py-1 bg-emerald-700 hover:bg-emerald-600 rounded disabled:opacity-50">
                  پذیرش گروهی</button>
                <button onClick={() => rule(c.cause, 'deferred', 'در انتظار پیش‌نیاز')}
                  disabled={busy === c.cause}
                  className="px-2 py-1 bg-slate-700 hover:bg-slate-600 rounded disabled:opacity-50">
                  تعویق</button>
                <button onClick={() => rule(c.cause, 'wont_fix', 'رفع نمی‌شود')}
                  disabled={busy === c.cause}
                  className="px-2 py-1 bg-slate-700 hover:bg-slate-600 rounded disabled:opacity-50">
                  رفع نمی‌شود</button>
              </div>
            )}
          </div>))}
      </div>
    </div>
  )
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
      if (!f) { f = { irc, name: name || irc, issues: new Set(), formulary: null,
                      announced: null, reference: null, gap: null }; m.set(irc, f) }
      else if (name && (f.name === f.irc)) f.name = name
      return f
    }
    const cov = data?.coverage, nfi = data?.nfi
    cov?.price_conflicts.forEach(c => {
      const f = at(c.irc, c.name_fa); f.issues.add('price')
      f.announced = c.announced_price; f.reference = c.reference_price; f.gap = c.gap_pct
    })
    cov?.review.forEach((r: any) => {
      if (!r?.irc) return
      const f = at(String(r.irc), r.name)
      f.issues.add('review')
      // the formulary's own wording — the left-hand side of the incompatibility
      f.formulary = f.formulary || r.row?.drug_name || null
      if (f.reference == null && r.entry?.reference_price != null) f.reference = r.entry.reference_price
    })
    // unmatched formulary rows are incompatibilities too: they have a formulary
    // item but NO target in the catalog. Keyed synthetically (no irc exists).
    ;(cov?.unmatched ?? []).forEach((u: any, i: number) => {
      const text = u?.row?.drug_name
      if (!text) return
      const f = at(`نامنطبق#${i}`, text)
      f.issues.add('unmatched')
      f.formulary = text
      if (f.reference == null && u?.row?.reference_price != null) f.reference = Number(u.row.reference_price)
    })
    nfi?.no_price.forEach(x => at(x.irc, x.name_fa).issues.add('no_price'))
    nfi?.no_generic.forEach(x => at(x.irc, x.name_fa).issues.add('no_generic'))
    nfi?.no_country.forEach(x => at(x.irc, x.name_fa).issues.add('no_country'))
    nfi?.no_atc.forEach(x => at(x.irc, x.name_fa).issues.add('no_atc'))
    return [...m.values()]
  }, [data])

  const chipCounts = useMemo(() => {
    const c = { price: 0, review: 0, unmatched: 0, no_price: 0,
                no_generic: 0, no_country: 0, no_atc: 0 } as Record<IssueType, number>
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
      <NfiIntegrityCard onError={onError} />
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
                <tr><th className="px-3 py-2 text-right font-medium">قلم دارونامه</th>
                    <th className="px-3 py-2 text-right font-medium">مورد ناسازگار هدف</th>
                    <th className="px-3 py-2 text-left font-medium">قیمت</th></tr>}>
                {shown.map(f => {
                  const unmatchedOnly = f.issues.has('unmatched')
                  return (
                  <tr key={f.irc} onClick={() => !unmatchedOnly && setSelectedIrc(f.irc)}
                    className={`transition-colors ${unmatchedOnly ? '' : 'cursor-pointer'} ${
                      selectedIrc === f.irc ? 'bg-indigo-500/15' : 'hover:bg-slate-700/25'}`}>
                    {/* 1 — what the insurer's formulary actually says */}
                    <td className="px-3 py-2 text-right align-top">
                      <div className="text-slate-200">{f.formulary || f.name}</div>
                      <span className="flex flex-wrap gap-1 mt-1">
                        {[...f.issues].map(iss => (
                          <span key={iss} className={`text-[10px] px-1.5 py-0.5 rounded border ${issueTone(iss, f.gap)}`}>
                            {ISSUE_LABEL[iss]}</span>))}
                      </span>
                    </td>
                    {/* 2 — the catalog product it conflicts with (or none) */}
                    <td className="px-3 py-2 text-right align-top">
                      {unmatchedOnly ? (
                        <span className="text-slate-500">در کاتالوگ یافت نشد</span>
                      ) : (<>
                        <div className="text-slate-200">{f.name}</div>
                        <div className="text-[11px] font-mono text-slate-500 tabular-nums">{f.irc}</div>
                      </>)}
                    </td>
                    {/* 3 — the money: announced vs insurer reference, and the gap */}
                    <td className="px-3 py-2 text-left align-top whitespace-nowrap tabular-nums">
                      <div className="text-slate-300">
                        {f.announced != null && <span title="قیمت اعلامی کاتالوگ">{fa(f.announced)}</span>}
                        {f.announced != null && f.reference != null && <span className="text-slate-600"> → </span>}
                        {f.reference != null && <span title="قیمت مرجع بیمه‌گر">{fa(f.reference)}</span>}
                        {f.announced == null && f.reference == null && <span className="text-slate-600">—</span>}
                      </div>
                      {f.gap != null && (
                        <div className={`text-[11px] font-semibold ${gapTone(f.gap)}`}>
                          {f.gap > 0 ? '+' : ''}{fa(f.gap)}٪
                        </div>)}
                    </td>
                  </tr>)})}
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
interface EnrichVariant {
  dosage_form: string | null; route?: string | null; strength: string | null
  concentration?: string | null; pack_size?: string | null; container?: string | null
  salt_form?: string | null; category?: string | null
  scientific_name?: string | null; marker?: string | null
  size?: string | null; material?: string | null; sterility?: string | null
  brand_name: string | null; manufacturer: string | null; notes: string | null
}
interface Suggestion {
  id: string; key: string; raw_name: string; irc: string | null
  generic_name: string | null; brand_name: string | null; manufacturer: string | null
  country: string | null; dosage_form: string | null; strengths: string[] | null
  variants: EnrichVariant[] | null; item_kind: string
  notes: string | null; sources: string[] | null; researched_by: string
  confidence: number | null; status: string; fs_score?: number | null
  context?: {
    insurer?: string; reason?: string; reference_price?: number | string
    share_pct?: number | string; covered?: unknown; ceiling?: number | string
    row?: Record<string, unknown>
  } | null
  nfi_candidates?: NfiCandidate[]
}
interface NfiCandidate {
  irc: string; name_fa: string; generic_name: string | null
  dosage_form: string | null; strength: string | null
  announced_price: number | null; manufacturer: string | null
  country: string | null; coverage: Record<string, any> | null; similarity: number
}
const INSURER_FA: Record<string, string> = {
  tamin: 'تأمین اجتماعی', salamat: 'بیمه سلامت', armed: 'نیروهای مسلح',
}
const KIND_FA: Record<string, string> = {
  herbal: 'گیاهی', device: 'تجهیزات', bulk: 'مادهٔ اولیه', supply: 'لوازم/ظرف', supplement: 'مکمل', other: 'سایر',
}
interface EnrichRunStatus {
  running: boolean; phase: string; total: number; done: number; saved: number
  skipped: number; failed: number; provider: string; workers: number
  pace_sec: number
  search_backend?: string
  current: string
  error: string | null
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
  const [workers, setWorkers] = useState(5)
  const [provider, setProvider] = useState('mistral')
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
  // suspect-first: low FS score = the model doubts the research matches the name
  const rows = [...(sugg?.suggestions || [])]
    .sort((a, b) => (a.fs_score ?? 999) - (b.fs_score ?? 999))

  const startRun = async (refresh = false) => {
    onMsg({ kind: 'ok', text: refresh ? 'پژوهش دوبارهٔ موارد قبلی آغاز شد…' : 'پژوهش آغاز شد…' })
    try {
      await pricingApi.enrichRun({ limit, min_confidence: 0.7, workers, provider, refresh })
      qc.invalidateQueries({ queryKey: ['enrich-run-status'] })
    } catch (e) { onError(e, 'شروع پژوهش ناموفق بود.') }
  }
  const stopRun = async () => {
    try {
      await pricingApi.enrichStop()
      onMsg({ kind: 'ok', text: 'درخواست توقف ثبت شد — پیشنهادهای ذخیره‌شده حفظ می‌شوند.' })
      qc.invalidateQueries({ queryKey: ['enrich-run-status'] })
    } catch (e) { onError(e, 'توقف ناموفق بود.') }
  }
  const testProvider = async () => {
    setBusy(true)
    try {
      const { data } = await pricingApi.enrichProviderTest(provider)
      const search = data.search_backend
        ? ` · جستجو: ${data.search_backend}${data.search_error ? ` ✗ (${data.search_error})` : ` ✓ (${data.search_results} نتیجه)`}`
        : ''
      onMsg({
        kind: data.ok === false ? 'err' : 'ok',
        text: `اتصال ${data.provider}${data.ok === false ? ' ناقص است' : ' برقرار است'} (${data.model || 'مدل پیش‌فرض'}): ${data.reply || 'OK'}${search}`,
      })
    } catch (e) { onError(e, `اتصال ${provider} برقرار نشد.`) }
    finally { setBusy(false) }
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
  const retrainIntel = async (mode: 'decisions' | 'bootstrap') => {
    setBusy(true)
    try {
      const { data } = await pricingApi.matchIntelRetrain(mode)
      const bands = Object.entries(data.price_bands || {})
        .map(([k, n]) => `${k}: ${fa(n as number)}`).join('، ')
      onMsg({ kind: 'ok', text:
        `هوش تطبیق آموزش دید (${mode === 'bootstrap' ? 'کل دارونامه‌ها' : 'تصمیم‌های شما'}) — `
        + `جفت‌ها: ${fa(data.pairs?.pos)}+ / ${fa(data.pairs?.neg)}− · `
        + `FS ${data.fs_armed ? 'فعال ✓' : 'غیرفعال (برچسب مثبت کافی نیست)'} · باند قیمت: ${bands || '—'}` })
    } catch (e) { onError(e, 'آموزش ناموفق بود.') }
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
        <label className="text-[12px] text-slate-400">موتور جستجو</label>
        <select value={provider} onChange={e => setProvider(e.target.value)}
          className="bg-slate-900 border border-slate-600 rounded px-2 py-1 text-sm">
          <option value="mistral">Mistral (کند — محدودیت نرخ)</option>
          <option value="gemini">Gemini (رایگان، استخراج)</option>
        </select>
        {provider === 'gemini' && run?.search_backend && (
          <span className={`text-[11px] px-2 py-0.5 rounded-full border ${
            run.search_backend !== 'duckduckgo'
              ? 'bg-orange-500/10 border-orange-500/40 text-orange-300'
              : 'bg-slate-700 border-slate-600 text-slate-300'}`}>
            جستجوی وب: {run.search_backend === 'youcom' ? 'You.com ✓'
              : run.search_backend === 'brave' ? 'Brave ✓' : 'DuckDuckGo (بدون کلید)'}
          </span>
        )}
        <label className="text-[12px] text-slate-400">تعداد در این اجرا</label>
        <input type="number" min={1} max={500} value={limit}
          onChange={e => setLimit(Math.max(1, Math.min(500, Number(e.target.value) || 1)))}
          className="w-20 bg-slate-900 border border-slate-600 rounded px-2 py-1 text-sm tabular-nums" />
        <label className="text-[12px] text-slate-400">هم‌زمانی</label>
        <input type="number" min={1} max={15} value={workers}
          onChange={e => setWorkers(Math.max(1, Math.min(15, Number(e.target.value) || 1)))}
          className="w-16 bg-slate-900 border border-slate-600 rounded px-2 py-1 text-sm tabular-nums" />
        <button onClick={() => startRun(false)} disabled={run?.running}
          className="px-3 py-1.5 text-sm rounded-md bg-fuchsia-600 hover:bg-fuchsia-500 disabled:opacity-40">
          {run?.running ? 'در حال پژوهش…' : 'شروع پژوهش'}
        </button>
        <button onClick={() => startRun(true)} disabled={run?.running}
          title="پژوهش دوبارهٔ همهٔ موارد «پیشنهادی» با آخرین قواعد و تغییرات هوش تطبیق"
          className="px-3 py-1.5 text-sm rounded-md bg-fuchsia-800 hover:bg-fuchsia-700 disabled:opacity-40">
          🔄 پژوهش دوباره
        </button>
        {run?.running && (
          <button onClick={stopRun} disabled={run.phase === 'cancelling'}
            className="px-3 py-1.5 text-sm rounded-md bg-red-700 hover:bg-red-600 disabled:opacity-40">
            {run.phase === 'cancelling' ? 'در حال توقف…' : '⏹ توقف'}
          </button>
        )}
        {!run?.running && (
          <button onClick={testProvider} disabled={busy}
            className="px-3 py-1.5 text-sm rounded-md bg-slate-700 hover:bg-slate-600 disabled:opacity-40">
            آزمون اتصال
          </button>
        )}
        <div className="mr-auto flex gap-2">
          <button onClick={() => retrainIntel('decisions')} disabled={busy}
            title="فقط جفت‌های تأیید/ردشدهٔ شما — برچسب‌های قطعی"
            className="px-2.5 py-1 text-[12px] rounded bg-cyan-800 hover:bg-cyan-700 disabled:opacity-40">
            🧠 آموزش از تصمیم‌ها</button>
          <button onClick={() => retrainIntel('bootstrap')} disabled={busy}
            title="خودآموزی ضعیف روی همهٔ ردیف‌های دارونامه‌ها — تطبیق‌های بسیار مطمئن لینکر به‌عنوان مثبت"
            className="px-2.5 py-1 text-[12px] rounded bg-cyan-900 hover:bg-cyan-800 disabled:opacity-40">
            🧠 آموزش بر کل دارونامه‌ها</button>
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
            {run.provider && <span>موتور: {run.provider}</span>}
            <span>هم‌زمانی: {fa(run.workers)}</span>
            {run.pace_sec > 0 && <span>فاصله فراخوانی: {run.pace_sec}s</span>}
            <span>پیشرفت: {fa(run.done)}/{fa(run.total)}</span>
            <span className="text-emerald-300">ثبت‌شده: {fa(run.saved)}</span>
            <span className="text-slate-400">ردشده: {fa(run.skipped)}</span>
            <span className="text-red-400">ناموفق: {fa(run.failed)}</span>
            <span>{run.elapsed_sec}s</span>
          </div>
          {/* current is self-describing now («۳ فعال: …» / «محدودیت نرخ؛ …») */}
          {run.current && <div className="text-slate-300 truncate">{run.current}</div>}
        </div>
      )}
      {run && !run.running && run.error &&
        <p className="text-[12px] text-red-400">خطای آخرین اجرا: {run.error}</p>}

      {/* per-item outcomes — without these a failed run shows only a count */}
      {(run?.recent || []).length > 0 && (
        <details className="text-[12px]" open={(run?.failed || 0) > 0}>
          <summary className="cursor-pointer text-slate-400 hover:text-slate-200">
            رویدادهای اخیر ({fa(run?.recent.length)})
          </summary>
          <ul className="mt-1.5 space-y-0.5 font-mono max-h-48 overflow-y-auto">
            {(run?.recent || []).slice().reverse().map((r, i) => {
              const bad = r.result.startsWith('failed')
              return (
                <li key={i} className="flex gap-2 border-b border-slate-700/40 pb-0.5">
                  <span className="text-slate-300 truncate max-w-[14rem]">{r.name}</span>
                  <span className={bad ? 'text-red-400' : 'text-emerald-400'}>{r.result}</span>
                </li>)
            })}
          </ul>
        </details>
      )}

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
                    <div className="font-semibold text-slate-100">
                      {s.raw_name}
                      {s.item_kind && s.item_kind !== 'drug' && (
                        <span className="mr-2 text-[10px] px-1.5 py-0.5 rounded bg-amber-500/15 border border-amber-500/40 text-amber-300 align-middle">
                          {KIND_FA[s.item_kind] || s.item_kind}
                        </span>
                      )}
                    </div>
                    <div className="text-slate-300">
                      {[s.generic_name, s.brand_name, s.manufacturer, s.country,
                        s.dosage_form, (s.strengths || []).join(' / ')]
                        .filter(Boolean).join(' · ') || '—'}
                    </div>
                    {(s.variants || []).length > 0 && (
                      <div className="flex flex-wrap gap-1 pt-0.5">
                        {(s.variants || []).map((v, i) => (
                          <span key={i} className="text-[11px] px-1.5 py-0.5 rounded bg-fuchsia-500/10 border border-fuchsia-500/30 text-fuchsia-200">
                            {[v.category, v.dosage_form, v.route, v.salt_form,
                              v.strength, v.concentration, v.size, v.material,
                              v.sterility, v.pack_size, v.container,
                              v.scientific_name, v.marker, v.brand_name]
                              .filter(Boolean).join(' · ') || '—'}
                          </span>
                        ))}
                      </div>
                    )}
                    {/* formulary side ↔ suspect NFI rows, compared side by side */}
                    {(s.context || (s.nfi_candidates || []).length > 0) && (
                      <div className="grid md:grid-cols-2 gap-2 pt-1.5">
                        <div className="rounded border border-amber-500/30 bg-amber-500/5 p-2 space-y-0.5">
                          <p className="text-[10px] text-amber-300/80">
                            دارونامهٔ {INSURER_FA[s.context?.insurer || ''] || s.context?.insurer || '—'}
                          </p>
                          <p className="text-[11px] text-slate-200">{s.raw_name}</p>
                          <div className="flex flex-wrap gap-x-3 text-[11px] text-slate-400 tabular-nums">
                            {s.context?.reference_price != null &&
                              <span>قیمت مرجع: {fa(Number(s.context.reference_price))}</span>}
                            {s.context?.share_pct != null &&
                              <span>سهم بیمه: {fa(Number(s.context.share_pct))}٪</span>}
                            {s.context?.covered != null &&
                              <span>{s.context.covered ? 'تحت پوشش' : 'بدون پوشش'}</span>}
                            {s.context?.reference_price == null && s.context?.share_pct == null &&
                              <span className="text-slate-600">داده‌ای از دارونامه ثبت نشده</span>}
                          </div>
                        </div>
                        <div className="rounded border border-sky-500/30 bg-sky-500/5 p-2 space-y-1">
                          <p className="text-[10px] text-sky-300/80">
                            نامزدهای کاتالوگ NFI ({fa((s.nfi_candidates || []).length)})
                          </p>
                          {(s.nfi_candidates || []).length === 0
                            ? <p className="text-[11px] text-slate-600">نامزد مشابهی یافت نشد</p>
                            : (s.nfi_candidates || []).map(c => (
                              <div key={c.irc} className="text-[11px] border-b border-slate-700/40 pb-0.5">
                                <div className="text-slate-200">{c.name_fa}</div>
                                <div className="flex flex-wrap gap-x-2 text-slate-500 tabular-nums">
                                  <span className="font-mono">{c.irc}</span>
                                  {c.strength && <span>{c.strength}</span>}
                                  {c.dosage_form && <span>{c.dosage_form}</span>}
                                  {c.announced_price != null && <span>{fa(c.announced_price)} ﷼</span>}
                                  <span className="text-sky-400">شباهت {c.similarity}</span>
                                </div>
                              </div>))}
                        </div>
                      </div>
                    )}
                    <div className="flex flex-wrap items-center gap-2 text-[11px] text-slate-400">
                      <span className="px-1.5 py-0.5 rounded bg-slate-700">{s.researched_by}</span>
                      {s.fs_score != null && (
                        <span className={`px-1.5 py-0.5 rounded border ${s.fs_score < 0
                          ? 'bg-red-500/10 border-red-500/40 text-red-300'
                          : 'bg-emerald-500/10 border-emerald-500/30 text-emerald-300'}`}
                          title="امتیاز هوش تطبیق — منفی یعنی پژوهش مشکوک است">
                          FS {s.fs_score}
                        </span>
                      )}
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

// ── کاتالوگ NFI — browse every column, correct any row ───────────────────────
interface CatalogItem {
  irc: string; name_fa: string; name_en: string | null; generic_name: string | null
  dosage_form: string | null; strength: string | null; brand_name: string | null
  manufacturer: string | null; atc: string | null; package_count: number | null
  gtin: string | null; erx_code: string | null; country: string | null
  license_owner: string | null; brand_owner: string | null
  license_valid_until: string | null; category: string | null
  is_generic: boolean | null; is_otc: boolean | null
  announced_price: number | null; last_invoice_price: number | null
  ingredient_key: string | null; coverage: Record<string, any> | null; source: string | null
}
// label, key, input kind — the owner-correctable surface of an NFI row
const EDIT_FIELDS: [string, keyof CatalogItem, 'text' | 'num' | 'bool'][] = [
  ['نام فارسی', 'name_fa', 'text'], ['نام لاتین', 'name_en', 'text'],
  ['ژنریک (مادهٔ مؤثره)', 'generic_name', 'text'], ['شکل دارویی', 'dosage_form', 'text'],
  ['قدرت', 'strength', 'text'], ['برند', 'brand_name', 'text'],
  ['تولیدکننده', 'manufacturer', 'text'], ['کشور', 'country', 'text'],
  ['ATC', 'atc', 'text'], ['تعداد در بسته', 'package_count', 'num'],
  ['بارکد (GTIN)', 'gtin', 'text'], ['کد نسخه الکترونیک', 'erx_code', 'text'],
  ['صاحب پروانه', 'license_owner', 'text'], ['صاحب برند', 'brand_owner', 'text'],
  ['اعتبار پروانه', 'license_valid_until', 'text'], ['دسته', 'category', 'text'],
  ['قیمت اعلامی', 'announced_price', 'num'], ['قیمت فاکتور', 'last_invoice_price', 'num'],
  ['ژنریک است', 'is_generic', 'bool'], ['OTC است', 'is_otc', 'bool'],
]
const MISSING_FILTERS: [string, string][] = [
  ['', 'همه'], ['price', 'بدون قیمت'], ['generic', 'بدون ژنریک'],
  ['country', 'بدون کشور'], ['atc', 'بدون ATC'], ['form', 'بدون شکل'],
  ['strength', 'بدون قدرت'],
]

function CatalogEditorPanel({ onMsg, onError }: {
  onMsg: (m: { kind: 'ok' | 'err'; text: string }) => void
  onError: (e: unknown, fallback: string) => void
}) {
  const qc = useQueryClient()
  const [q, setQ] = useState('')
  const [missing, setMissing] = useState('')
  const [offset, setOffset] = useState(0)
  const [openIrc, setOpenIrc] = useState<string | null>(null)
  const [draft, setDraft] = useState<Record<string, any>>({})
  const [busy, setBusy] = useState(false)
  const limit = 25

  const { data } = useQuery<{ total: number; items: CatalogItem[] }>({
    queryKey: ['catalog-items', q, missing, offset],
    queryFn: () => pricingApi.catalogItems({ q: q || undefined,
      missing: missing || undefined, limit, offset }).then(r => r.data),
  })
  const items = data?.items || []
  const open = items.find(i => i.irc === openIrc) || null

  const startEdit = (it: CatalogItem) => {
    setOpenIrc(it.irc)
    setDraft(Object.fromEntries(EDIT_FIELDS.map(([, k]) => [k, (it as any)[k]])))
  }
  const save = async () => {
    if (!open) return
    const changed: Record<string, unknown> = {}
    EDIT_FIELDS.forEach(([, k]) => {
      const before = (open as any)[k], after = draft[k]
      if (String(before ?? '') !== String(after ?? '')) changed[k as string] = after
    })
    if (!Object.keys(changed).length) { onMsg({ kind: 'ok', text: 'تغییری برای ذخیره نیست.' }); return }
    setBusy(true)
    try {
      const { data: res } = await pricingApi.catalogEdit(open.irc, changed, 'اصلاح دستی از کاتالوگ')
      onMsg({ kind: 'ok', text: `${fa(Object.keys(res.changed || {}).length)} فیلد در ${open.irc} اصلاح شد.` })
      qc.invalidateQueries({ queryKey: ['catalog-items'] })
      setOpenIrc(null)
    } catch (e) { onError(e, 'ذخیرهٔ اصلاح ناموفق بود.') } finally { setBusy(false) }
  }

  return (
    <div className="bg-slate-800/50 border border-violet-500/30 rounded-lg p-4 space-y-3" dir="rtl">
      <div className="flex items-baseline gap-2 flex-wrap">
        <h3 className="font-bold text-violet-200">🗂 کاتالوگ NFI</h3>
        <span className="text-[12px] text-slate-400">
          مرور همهٔ ستون‌ها و اصلاح هر قلم — ناسازگاری‌ها اغلب از خطای همین فهرست‌اند.
        </span>
      </div>

      <div className="flex flex-wrap items-center gap-2 text-[12px]">
        <input type="search" value={q} onChange={e => { setQ(e.target.value); setOffset(0) }}
          placeholder="جست‌وجو: IRC، نام، ژنریک، برند…"
          className="flex-1 min-w-[14rem] bg-slate-900 border border-slate-600 rounded px-3 py-1" />
        <select value={missing} onChange={e => { setMissing(e.target.value); setOffset(0) }}
          className="bg-slate-900 border border-slate-600 rounded px-2 py-1">
          {MISSING_FILTERS.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
        </select>
        <span className="text-slate-400">{fa(data?.total)} قلم</span>
      </div>

      <ScrollTable head={
        <tr><th className="px-3 py-2 text-right font-medium">نام</th>
            <th className="px-3 py-2 text-right font-medium">ژنریک</th>
            <th className="px-3 py-2 text-right font-medium">شکل / قدرت</th>
            <th className="px-3 py-2 text-left font-medium">قیمت</th>
            <th className="px-3 py-2 text-left font-medium"></th></tr>}>
        {items.map(it => (
          <tr key={it.irc} className="hover:bg-slate-700/25">
            <td className="px-3 py-2 text-right">
              <div className="text-slate-200">{it.name_fa}</div>
              <div className="text-[11px] font-mono text-slate-500">{it.irc}</div>
            </td>
            <td className="px-3 py-2 text-right text-slate-300">{it.generic_name || '—'}</td>
            <td className="px-3 py-2 text-right text-slate-400">
              {[it.dosage_form, it.strength].filter(Boolean).join(' · ') || '—'}
            </td>
            <td className="px-3 py-2 text-left tabular-nums text-slate-300">{fa(it.announced_price)}</td>
            <td className="px-3 py-2 text-left">
              <button onClick={() => startEdit(it)}
                className="px-2 py-0.5 text-[11px] rounded bg-violet-700 hover:bg-violet-600">ویرایش</button>
            </td>
          </tr>))}
      </ScrollTable>

      <div className="flex items-center gap-2 text-[12px]">
        <button onClick={() => setOffset(Math.max(0, offset - limit))} disabled={offset === 0}
          className="px-2 py-0.5 rounded bg-slate-700 hover:bg-slate-600 disabled:opacity-40">قبلی</button>
        <span className="text-slate-500">{fa(offset + 1)}–{fa(Math.min(offset + limit, data?.total || 0))}</span>
        <button onClick={() => setOffset(offset + limit)}
          disabled={offset + limit >= (data?.total || 0)}
          className="px-2 py-0.5 rounded bg-slate-700 hover:bg-slate-600 disabled:opacity-40">بعدی</button>
      </div>

      {open && (
        <div className="border border-violet-500/40 rounded-lg p-3 space-y-3">
          <div className="flex items-center gap-2 flex-wrap">
            <p className="font-semibold text-sm text-violet-200">ویرایش {open.name_fa}</p>
            <span className="text-[11px] font-mono text-slate-500">{open.irc}</span>
            <button onClick={() => setOpenIrc(null)}
              className="mr-auto text-slate-400 hover:text-slate-200 text-[12px]">بستن ✕</button>
          </div>
          <div className="grid sm:grid-cols-2 lg:grid-cols-3 gap-2">
            {EDIT_FIELDS.map(([label, key, kind]) => (
              <label key={key as string} className="space-y-0.5 text-[11px]">
                <span className="text-slate-400">{label}</span>
                {kind === 'bool' ? (
                  <select value={String(draft[key] ?? false)}
                    onChange={e => setDraft(d => ({ ...d, [key]: e.target.value === 'true' }))}
                    className="w-full bg-slate-900 border border-slate-600 rounded px-2 py-1">
                    <option value="true">بله</option><option value="false">خیر</option>
                  </select>
                ) : (
                  <input type={kind === 'num' ? 'number' : 'text'}
                    value={draft[key] ?? ''}
                    onChange={e => setDraft(d => ({ ...d, [key]: e.target.value }))}
                    className="w-full bg-slate-900 border border-slate-600 rounded px-2 py-1 tabular-nums" />
                )}
              </label>))}
          </div>
          <div className="flex items-center gap-2">
            <button onClick={save} disabled={busy}
              className="px-3 py-1.5 text-sm rounded-md bg-violet-600 hover:bg-violet-500 disabled:opacity-40">
              ذخیرهٔ اصلاح
            </button>
            <span className="text-[11px] text-slate-500">
              تغییر قیمت به‌صورت نقطهٔ تاریخ‌دار در تاریخچهٔ قیمت ثبت می‌شود؛ کلید ترکیب خودکار بازمحاسبه می‌گردد.
            </span>
          </div>
        </div>
      )}
    </div>
  )
}

// ── ⚖ تصمیم‌ها — the durable decision layer: crosswalk + field overrides ──────
interface CrosswalkRow {
  id: string; insurer: string; source_code: string | null; raw_key: string
  raw_name: string | null; irc: string | null; status: string
  reason: string | null; decided_at: string | null
}
interface OverrideRow {
  id: string; irc: string; field: string; value: string | null
  reason: string | null; decided_at: string | null
}
const DECISION_REASON_FA: Record<string, string> = {
  wrong_product: 'داروی دیگر', wrong_strength: 'قدرت اشتباه', wrong_form: 'شکل اشتباه',
  wrong_brand: 'برند اشتباه', wrong_pack: 'بستهٔ اشتباه',
  price_implausible: 'قیمت نامعقول', other: 'سایر',
}

function DecisionsPanel({ onMsg, onError }: {
  onMsg: (m: { kind: 'ok' | 'err'; text: string }) => void
  onError: (e: unknown, fallback: string) => void
}) {
  const qc = useQueryClient()
  const [insurer, setInsurer] = useState('')
  const [status, setStatus] = useState('')
  const [busy, setBusy] = useState(false)

  const { data: cw } = useQuery<{ total: number; entries: CrosswalkRow[] }>({
    queryKey: ['crosswalk', insurer, status],
    queryFn: () => pricingApi.crosswalkList({ insurer: insurer || undefined,
      status: status || undefined, limit: 300 }).then(r => r.data),
  })
  const { data: ov } = useQuery<{ total: number; overrides: OverrideRow[] }>({
    queryKey: ['overrides'],
    queryFn: () => pricingApi.overridesList().then(r => r.data),
  })

  const revoke = async (o: OverrideRow) => {
    if (!window.confirm(
      `لغو اصلاح «${o.field}» برای ${o.irc}؟ مقدار به آنچه منبع منتشر می‌کند برمی‌گردد.`)) return
    setBusy(true)
    try {
      await pricingApi.overrideDelete(o.id)
      onMsg({ kind: 'ok', text: `اصلاح ${o.field} لغو شد — از خزش بعدی، مقدار منبع برمی‌گردد.` })
      qc.invalidateQueries({ queryKey: ['overrides'] })
    } catch (e) { onError(e, 'لغو اصلاح ناموفق بود.') } finally { setBusy(false) }
  }

  return (
    <div className="space-y-4" dir="rtl">
      {/* crosswalk: insurer row ↔ our product, decided once */}
      <div className="bg-slate-800/50 border border-teal-500/30 rounded-lg p-4 space-y-3">
        <div className="flex items-baseline gap-2 flex-wrap">
          <h3 className="font-bold text-teal-200">⚖ نگاشت‌های قطعی (Crosswalk)</h3>
          <span className="text-[12px] text-slate-400">
            هر رأی شما یک‌بار ثبت می‌شود و در همهٔ برداشت‌های بعدی بدون بازبینی دوباره اعمال می‌گردد.
          </span>
          <span className="mr-auto flex gap-2">
            <button onClick={async () => {
              setBusy(true)
              try {
                const { data } = await pricingApi.canonicalExport()
                const c = data.counts || {}
                onMsg({ kind: 'ok', text:
                  `بستهٔ متعارف نوشته شد — کاتالوگ ${fa(c.catalog)}، نگاشت ${fa(c.crosswalk)}، اصلاح ${fa(c.overrides)}، قیمت جاری ${fa(c.prices_current)} (data/canonical/)` })
              } catch (e) { onError(e, 'برون‌سپاری بسته ناموفق بود.') } finally { setBusy(false) }
            }} disabled={busy}
              title="کاتالوگ + دارونامه‌های حل‌شده + نگاشت‌ها + اصلاح‌ها + قیمت‌های جاری، با مانیفست checksum"
              className="px-2.5 py-1 text-[12px] rounded bg-teal-800 hover:bg-teal-700 disabled:opacity-40">
              📦 برون‌سپاری بستهٔ متعارف
            </button>
            <button onClick={async () => {
              setBusy(true)
              try {
                const { data } = await pricingApi.canonicalImport()
                onMsg({ kind: 'ok', text: `لایهٔ تصمیم بازخوانی شد — ${fa(data.crosswalk)} نگاشت، ${fa(data.overrides)} اصلاح.` })
                qc.invalidateQueries({ queryKey: ['crosswalk'] })
                qc.invalidateQueries({ queryKey: ['overrides'] })
              } catch (e) { onError(e, 'بازخوانی بسته ناموفق بود.') } finally { setBusy(false) }
            }} disabled={busy}
              className="px-2.5 py-1 text-[12px] rounded bg-slate-700 hover:bg-slate-600 disabled:opacity-40">
              بازخوانی لایهٔ تصمیم
            </button>
          </span>
        </div>
        <div className="flex flex-wrap items-center gap-2 text-[12px]">
          <select value={insurer} onChange={e => setInsurer(e.target.value)}
            className="bg-slate-900 border border-slate-600 rounded px-2 py-1">
            <option value="">همهٔ بیمه‌گرها</option>
            <option value="tamin">تأمین اجتماعی</option>
            <option value="salamat">بیمه سلامت</option>
            <option value="armed">نیروهای مسلح</option>
          </select>
          <select value={status} onChange={e => setStatus(e.target.value)}
            className="bg-slate-900 border border-slate-600 rounded px-2 py-1">
            <option value="">همهٔ وضعیت‌ها</option>
            <option value="confirmed">تأییدشده</option>
            <option value="rejected">ردشده</option>
          </select>
          <span className="text-slate-400">{fa(cw?.total)} تصمیم</span>
        </div>
        {(cw?.entries || []).length === 0
          ? <Empty text="هنوز تصمیمی ثبت نشده — با «اعمال اجرا»ی بعدی، رأی‌های شما اینجا انباشته می‌شوند." />
          : <ScrollTable head={
              <tr><th className="px-3 py-2 text-right font-medium">قلم دارونامه</th>
                  <th className="px-3 py-2 text-right font-medium">حکم</th>
                  <th className="px-3 py-2 text-right font-medium">محصول (IRC)</th>
                  <th className="px-3 py-2 text-right font-medium">تاریخ</th></tr>}>
              {(cw?.entries || []).map(e => (
                <tr key={e.id} className="hover:bg-slate-700/25">
                  <td className="px-3 py-2 text-right">
                    <div className="text-slate-200">{e.raw_name || e.raw_key}</div>
                    <div className="text-[11px] text-slate-500">
                      {e.insurer}{e.source_code && <span className="font-mono"> · کد {e.source_code}</span>}
                    </div>
                  </td>
                  <td className="px-3 py-2 text-right">
                    <span className={`text-[11px] px-1.5 py-0.5 rounded border ${
                      e.status === 'confirmed'
                        ? 'bg-emerald-500/15 border-emerald-500/40 text-emerald-300'
                        : 'bg-red-500/15 border-red-500/40 text-red-300'}`}>
                      {e.status === 'confirmed' ? 'تأیید' : 'رد'}
                      {e.reason && ` — ${DECISION_REASON_FA[e.reason] || e.reason}`}
                    </span>
                  </td>
                  <td className="px-3 py-2 text-right font-mono text-[11px] text-slate-400">
                    {e.irc || '—'}</td>
                  <td className="px-3 py-2 text-right text-[11px] text-slate-500">
                    {e.decided_at ? new Date(e.decided_at).toLocaleDateString('fa-IR') : '—'}</td>
                </tr>))}
            </ScrollTable>}
      </div>

      {/* field overrides: corrections that outlive crawls */}
      <div className="bg-slate-800/50 border border-violet-500/30 rounded-lg p-4 space-y-3">
        <div className="flex items-baseline gap-2 flex-wrap">
          <h3 className="font-bold text-violet-200">🛡 اصلاح‌های ماندگار کاتالوگ</h3>
          <span className="text-[12px] text-slate-400">
            این مقادیر پس از هر خزش NFI دوباره اعمال می‌شوند؛ لغو ⇒ بازگشت به مقدار منبع.
          </span>
          <span className="mr-auto text-[12px] text-slate-400">{fa(ov?.total)} اصلاح</span>
        </div>
        {(ov?.overrides || []).length === 0
          ? <Empty text="اصلاح ماندگاری ثبت نشده — ویرایش‌های کاتالوگ به‌صورت پیش‌فرض اینجا می‌آیند." />
          : <ScrollTable head={
              <tr><th className="px-3 py-2 text-right font-medium">محصول</th>
                  <th className="px-3 py-2 text-right font-medium">فیلد</th>
                  <th className="px-3 py-2 text-right font-medium">مقدار مالک</th>
                  <th className="px-3 py-2 text-right font-medium">دلیل</th>
                  <th className="px-3 py-2 text-left font-medium"></th></tr>}>
              {(ov?.overrides || []).map(o => (
                <tr key={o.id} className="hover:bg-slate-700/25">
                  <td className="px-3 py-2 text-right font-mono text-[11px] text-slate-400">{o.irc}</td>
                  <td className="px-3 py-2 text-right text-slate-300">{o.field}</td>
                  <td className="px-3 py-2 text-right text-slate-200">
                    {o.value ?? <span className="text-slate-500">(خالی اجباری)</span>}</td>
                  <td className="px-3 py-2 text-right text-[11px] text-slate-500">{o.reason || '—'}</td>
                  <td className="px-3 py-2 text-left">
                    <button onClick={() => revoke(o)} disabled={busy}
                      className="px-2 py-0.5 text-[11px] rounded bg-red-800 hover:bg-red-700 disabled:opacity-40">
                      لغو</button>
                  </td>
                </tr>))}
            </ScrollTable>}
      </div>
    </div>
  )
}

// ── برداشت تأمین — supervises the standalone DevExpress-replay harvester ─────
interface TaminStatus {
  running: boolean; phase: string; page: number; page_count: number; rows: number
  pct: number; elapsed_sec: number; error: string | null; log: string[]
  outputs?: { csv?: { rows?: number; bytes: number; mtime: number }
              json?: { bytes: number; mtime: number } }
}

function TaminHarvestPanel({ onMsg, onError }: {
  onMsg: (m: { kind: 'ok' | 'err'; text: string }) => void
  onError: (e: unknown, fallback: string) => void
}) {
  const qc = useQueryClient()
  const [inputHtml, setInputHtml] = useState('')
  const [delay, setDelay] = useState(1)
  const [timeout_, setTimeout_] = useState(120)
  const [busy, setBusy] = useState(false)

  const { data: st } = useQuery<TaminStatus>({
    queryKey: ['tamin-harvest'],
    queryFn: () => pricingApi.taminHarvestStatus().then(r => r.data),
    refetchInterval: q => (q.state.data?.running ? 2_000 : 20_000),
  })

  const start = async () => {
    setBusy(true)
    try {
      await pricingApi.taminHarvestStart({
        input_html: inputHtml || undefined, delay, timeout: timeout_ })
      onMsg({ kind: 'ok', text: 'برداشت تأمین آغاز شد — ادامه از آخرین صفحهٔ ذخیره‌شده.' })
      qc.invalidateQueries({ queryKey: ['tamin-harvest'] })
    } catch (e) { onError(e, 'شروع برداشت ناموفق بود.') } finally { setBusy(false) }
  }
  const stop = async () => {
    try {
      await pricingApi.taminHarvestStop()
      onMsg({ kind: 'ok', text: 'توقف درخواست شد — ردیف‌های ذخیره‌شده حفظ می‌شوند.' })
      qc.invalidateQueries({ queryKey: ['tamin-harvest'] })
    } catch (e) { onError(e, 'توقف ناموفق بود.') }
  }

  const out = st?.outputs || {}
  return (
    <div className="bg-slate-800/50 border border-emerald-500/25 rounded-lg p-4 space-y-4" dir="rtl">
      <div className="flex items-baseline gap-2 flex-wrap">
        <h3 className="font-bold text-emerald-200">برداشت دارونامهٔ تأمین اجتماعی</h3>
        <span className="text-[12px] text-slate-400">
          صفحه‌به‌صفحه از سامانهٔ ASPxGridView؛ با «ادامه» اجرای نیمه‌تمام از همان‌جا دنبال می‌شود.
        </span>
      </div>

      {/* settings — defaults match the command the owner runs by hand */}
      <div className="grid sm:grid-cols-3 gap-2 text-[12px]">
        <label className="space-y-1">
          <span className="text-slate-400">فایل HTML شروع</span>
          <input value={inputHtml} onChange={e => setInputHtml(e.target.value)}
            placeholder="پیش‌فرض: Downloads/معاونت درمان…html"
            className="w-full bg-slate-900 border border-slate-600 rounded px-2 py-1 font-mono text-[11px]" />
        </label>
        <label className="space-y-1">
          <span className="text-slate-400">مکث بین درخواست‌ها (ثانیه)</span>
          <input type="number" min={0} max={30} value={delay}
            onChange={e => setDelay(Number(e.target.value) || 0)}
            className="w-full bg-slate-900 border border-slate-600 rounded px-2 py-1 tabular-nums" />
        </label>
        <label className="space-y-1">
          <span className="text-slate-400">مهلت هر درخواست (ثانیه)</span>
          <input type="number" min={10} max={600} value={timeout_}
            onChange={e => setTimeout_(Number(e.target.value) || 120)}
            className="w-full bg-slate-900 border border-slate-600 rounded px-2 py-1 tabular-nums" />
        </label>
      </div>

      <div className="flex items-center gap-2 flex-wrap">
        <button onClick={start} disabled={busy || st?.running}
          className="px-3 py-1.5 text-sm rounded-md bg-emerald-600 hover:bg-emerald-500 disabled:opacity-40">
          {st?.running ? 'در حال برداشت…' : 'شروع برداشت'}
        </button>
        {st?.running && (
          <button onClick={stop}
            className="px-3 py-1.5 text-sm rounded-md bg-red-700 hover:bg-red-600">⏹ توقف</button>)}
        <span className="text-[11px] text-slate-500">
          پروکسی ایران باید فعال باشد؛ خطاهای شبکه تا ۲۰ بار بازآزمایی می‌شوند.
        </span>
      </div>

      {/* live progress */}
      {(st?.running || st?.page) ? (
        <div className="space-y-1.5">
          <div className="flex flex-wrap gap-x-5 text-[12px] font-mono">
            <span className={st?.running ? 'text-emerald-300' : 'text-slate-400'}>
              مرحله: {st?.phase}
            </span>
            <span>صفحه: {fa(st?.page)}/{fa(st?.page_count)}</span>
            <span>ردیف‌های این اجرا: {fa(st?.rows)}</span>
            <span>{st?.elapsed_sec}s</span>
          </div>
          <div className="h-1.5 bg-slate-700 rounded-full overflow-hidden">
            <div className="h-full bg-emerald-500 transition-all"
                 style={{ width: `${Math.min(100, st?.pct || 0)}%` }} />
          </div>
          {st?.error && <p className="text-[12px] text-red-400">{st.error}</p>}
        </div>
      ) : null}

      {/* produced files — what the coverage source then ingests */}
      <div className="flex flex-wrap gap-x-6 gap-y-1 text-[11px] text-slate-400 font-mono">
        {out.csv
          ? <span>CSV: {fa(out.csv.rows)} ردیف · {fa(Math.round(out.csv.bytes / 1024))}KB ·
              {' '}{new Date(out.csv.mtime * 1000).toLocaleString('fa-IR')}</span>
          : <span className="text-slate-600">هنوز CSV تولید نشده.</span>}
        {out.json && <span>JSON: {fa(Math.round(out.json.bytes / 1024))}KB</span>}
      </div>

      {(st?.log || []).length > 0 && (
        <details className="text-[11px]">
          <summary className="cursor-pointer text-slate-400 hover:text-slate-200">
            گزارش اجرا ({fa(st?.log.length)} خط)
          </summary>
          <pre className="mt-1 max-h-48 overflow-y-auto whitespace-pre-wrap text-slate-500 font-mono">
            {(st?.log || []).join('\n')}
          </pre>
        </details>
      )}
    </div>
  )
}

// ── تاریخچهٔ قیمت — dated price series: stale list + per-drug timeline ────────
interface StaleRow { irc: string; value: number; since: string | null; source: string | null }
interface PricePoint {
  price_type: string; insurer: string | null; value: number; source: string | null
  current: boolean; valid_from: string | null; valid_to: string | null
}
const PTYPE_FA: Record<string, string> = {
  announced: 'اعلامی', invoice: 'فاکتور', insurer_reference: 'مرجع بیمه',
}

function PriceHistoryPanel({ onMsg, onError }: {
  onMsg: (m: { kind: 'ok' | 'err'; text: string }) => void
  onError: (e: unknown, fallback: string) => void
}) {
  const qc = useQueryClient()
  const [ageDays, setAgeDays] = useState(180)
  const [openIrc, setOpenIrc] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  const { data: stale } = useQuery<{ count: number; max_age_days: number; samples: StaleRow[] }>({
    queryKey: ['price-stale', ageDays],
    queryFn: () => pricingApi.priceHistoryStale(ageDays).then(r => r.data),
  })
  const { data: series } = useQuery<{ irc: string; history: PricePoint[] }>({
    queryKey: ['price-series', openIrc],
    queryFn: () => pricingApi.priceHistory(openIrc as string).then(r => r.data),
    enabled: !!openIrc,
  })

  const backfill = async () => {
    setBusy(true)
    try {
      const { data } = await pricingApi.priceHistoryBackfill()
      onMsg({ kind: 'ok', text: `${fa(data.recorded)} نقطهٔ قیمت از کاتالوگ ثبت شد.` })
      qc.invalidateQueries({ queryKey: ['price-stale'] })
    } catch (e) { onError(e, 'ثبت اولیهٔ قیمت‌ها ناموفق بود.') } finally { setBusy(false) }
  }

  return (
    <div className="bg-slate-800/50 border border-cyan-500/30 rounded-lg p-4 space-y-3" dir="rtl">
      <div className="flex items-center gap-2 flex-wrap">
        <h3 className="font-bold text-cyan-200">📉 تاریخچهٔ قیمت</h3>
        <span className="text-[12px] text-slate-400">
          هر تغییر قیمت یک نقطهٔ تاریخ‌دار است؛ «کهنگی» یک پرس‌وجو است، نه حدس.
        </span>
        <button onClick={backfill} disabled={busy}
          title="ثبت قیمت‌های فعلی کاتالوگ به‌عنوان نقطهٔ شروع سری زمانی"
          className="mr-auto px-2.5 py-1 text-[12px] rounded bg-slate-700 hover:bg-slate-600 disabled:opacity-40">
          ثبت اولیه از کاتالوگ
        </button>
      </div>

      <div className="flex flex-wrap items-center gap-2 text-[12px]">
        <label className="text-slate-400">کهنه‌تر از</label>
        <select value={ageDays} onChange={e => setAgeDays(Number(e.target.value))}
          className="bg-slate-900 border border-slate-600 rounded px-2 py-1">
          <option value={90}>۹۰ روز</option>
          <option value={180}>۱۸۰ روز</option>
          <option value={365}>۱ سال</option>
        </select>
        <span className={`px-2 py-0.5 rounded-full border ${(stale?.count || 0) > 0
          ? 'bg-amber-500/10 border-amber-500/40 text-amber-300'
          : 'bg-emerald-500/10 border-emerald-500/30 text-emerald-300'}`}>
          {fa(stale?.count)} قیمت کهنه
        </span>
      </div>

      {(stale?.samples || []).length === 0
        ? <Empty text="قیمت کهنه‌ای در این بازه نیست ✓" />
        : <div className="max-h-56 overflow-y-auto space-y-0.5">
            {(stale?.samples || []).map(s => (
              <div key={s.irc} className="flex flex-wrap items-center gap-3 text-[12px] font-mono
                                          border-b border-slate-700/50 pb-1">
                <button onClick={() => setOpenIrc(openIrc === s.irc ? null : s.irc)}
                  className="text-cyan-300 hover:text-cyan-100">{s.irc}</button>
                <span className="tabular-nums">{fa(s.value)} ﷼</span>
                <span className="text-slate-500">
                  از {s.since ? new Date(s.since).toLocaleDateString('fa-IR') : '—'}
                </span>
                {s.source && <span className="text-slate-600">{s.source}</span>}
              </div>))}
          </div>}

      {openIrc && (
        <div className="border border-slate-700 rounded p-2 space-y-1">
          <p className="text-[12px] font-semibold text-slate-300">روند قیمت — {openIrc}</p>
          {(series?.history || []).length === 0
            ? <Empty text="نقطه‌ای ثبت نشده." />
            : <div className="max-h-48 overflow-y-auto space-y-0.5 font-mono text-[11px]">
                {(series?.history || []).map((p, i) => (
                  <div key={i} className="flex flex-wrap items-center gap-3 border-b border-slate-700/40 pb-0.5">
                    <span className={p.current ? 'text-emerald-300' : 'text-slate-500'}>
                      {p.current ? '● جاری' : '○ قبلی'}
                    </span>
                    <span className="text-slate-300">{PTYPE_FA[p.price_type] || p.price_type}</span>
                    {p.insurer && <span className="text-indigo-300">{p.insurer}</span>}
                    <span className="tabular-nums">{fa(p.value)} ﷼</span>
                    <span className="text-slate-500">
                      {p.valid_from ? new Date(p.valid_from).toLocaleDateString('fa-IR') : '—'}
                      {' → '}
                      {p.valid_to ? new Date(p.valid_to).toLocaleDateString('fa-IR') : 'اکنون'}
                    </span>
                    {p.source && <span className="text-slate-600">{p.source}</span>}
                  </div>))}
              </div>}
        </div>
      )}
    </div>
  )
}
