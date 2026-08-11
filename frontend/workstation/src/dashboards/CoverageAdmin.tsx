/**
 * CoverageAdmin — دارونامه sources, harvest & review. Per-insurer source cards
 * (probe → detect format → save column overrides), background harvest behind the
 * global proxy lock, staged runs with a diff vs live coverage, and preview→اعمال.
 * The one-shot upload card (instant apply) also lives here, moved from DrugCatalogAdmin.
 */
import { useEffect, useMemo, useRef, useState, type ReactNode } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { pricingApi, apiErrorText } from '../lib/api'
import { useLang } from '../lib/i18n'

type Pair = readonly [string, string]

// Panel-wide formatter. The hook is the source of truth for locale digits; this
// module-level helper exists only for the handful of non-component call sites.
const fa = (n: number | null | undefined) =>
  n == null ? '—' : new Intl.NumberFormat(
    localStorage.getItem('pharmpilot_lang') === 'en' ? 'en-US' : 'fa-IR').format(n)

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

const STRATEGIES: readonly (readonly [string, Pair])[] = [
  ['auto', ['Auto-detect', 'تشخیص خودکار']],
  ['file_url', ['Direct file (Excel/CSV)', 'فایل مستقیم (Excel/CSV)']],
  ['html_table', ['HTML table', 'جدول HTML']],
  ['paginated_html', ['Paginated HTML', 'HTML صفحه‌بندی‌شده']],
  ['json_api', ['JSON API', 'JSON API']],
] as const
const ROLES = ['irc', 'gtin', 'drug_name', 'covered', 'share_pct',
               'reference_price', 'ceiling', 'inpatient', 'ignore'] as const

// Tabs mirror the real workflow order: configure → fetch → review → fix → price.
const TABS = [
  { id: 'sources', label: ['Sources', 'منابع'] },
  { id: 'tamin',   label: ['Tamin harvest', 'برداشت تأمین'] },
  { id: 'runs',    label: ['Runs', 'اجراها'] },
  { id: 'issues',  label: ['Inconsistencies', 'ناسازگاری‌ها'] },
  { id: 'enrich',  label: ['✨ Enrichment', '✨ غنی‌سازی'] },
  { id: 'catalog', label: ['🗂 NFI catalog', '🗂 کاتالوگ NFI'] },
  { id: 'prices',  label: ['📉 Prices', '📉 قیمت‌ها'] },
  { id: 'decisions', label: ['⚖ Decisions', '⚖ تصمیم‌ها'] },
] as const satisfies ReadonlyArray<{ id: string; label: Pair }>
type TabId = typeof TABS[number]['id']

export default function CoverageAdmin() {
  const { t, tp, n, dir, date, dateTime } = useLang()
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
      err(e, t('Detection failed.', 'تشخیص ناموفق بود.'))
    }
  }
  const doHarvest = async (s: Source) => {
    setMsg(null)
    try {
      await pricingApi.coverageHarvest(s.id)
      qc.invalidateQueries({ queryKey: ['coverage-harvest-status'] })
    } catch (e) { err(e, t('Could not start the harvest.', 'شروع برداشت ناموفق بود.')) }
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
      setMsg({ kind: 'ok', text: t('Saved.', 'ذخیره شد.') })
    } catch (e) { err(e, t('Save failed.', 'ذخیره ناموفق بود.')) }
  }

  const locked = !!(hs?.lock_holder || sources?.sources?.[0]?.lock_holder)

  const runList = runs?.runs || []
  const pendingReview = runList.filter(r => r.status === 'parsed').length
  const lastRun = runList[0]

  return (
    <div className="p-4 pb-10 space-y-4 text-slate-100" dir={dir}>
      {/* ── header: title + at-a-glance state (no scrolling to learn status) ── */}
      <header className="space-y-3">
        <div className="flex items-baseline gap-3 flex-wrap">
          <h2 className="text-lg font-bold">{t('Insurance coverage', 'پوشش بیمه')}</h2>
          <span className="text-[12px] text-slate-500">{t('Insurer formularies — harvest, review and apply',
                                                          'دارونامهٔ بیمه‌گرها — برداشت، بازبینی و اعمال')}</span>
        </div>
        <div className="grid grid-cols-2 md:grid-cols-4 gap-2">
          <Stat label={t('Sources', 'منابع')} value={n((sources?.sources || []).length)}
                hint={locked ? t('Harvest lock held', 'قفل برداشت فعال') : t('Ready', 'آماده')}
                tone={locked ? 'amber' : 'slate'} />
          <Stat label={t('Runs awaiting approval', 'اجرای در انتظار تأیید')} value={n(pendingReview)}
                hint={pendingReview ? t('Needs review', 'نیازمند بازبینی') : t('Nothing pending', 'موردی نیست')}
                tone={pendingReview ? 'amber' : 'emerald'} />
          <Stat label={t('Last run', 'آخرین اجرا')} value={lastRun?.insurer || '—'}
                hint={date(lastRun?.finished_at, { short: true })} />
          <Stat label={t('Harvest state', 'وضعیت برداشت')}
                value={hs?.running ? t('running', 'در حال اجرا') : t('idle', 'بی‌کار')}
                hint={hs?.running ? `${hs.insurer} · ${hs.phase}` : t('Ready to start', 'آمادهٔ شروع')}
                tone={hs?.running ? 'indigo' : 'slate'} />
        </div>
      </header>

      {/* ── one concern per tab: the page no longer stacks every panel at once ── */}
      <nav className="flex gap-1 border-b border-slate-700 overflow-x-auto" role="tablist">
        {TABS.map(tb => (
          <button key={tb.id} role="tab" aria-selected={tab === tb.id}
            onClick={() => setTab(tb.id)}
            className={`px-3 py-2 text-sm whitespace-nowrap border-b-2 -mb-px transition-colors ${
              tab === tb.id
                ? 'border-indigo-400 text-indigo-200'
                : 'border-transparent text-slate-400 hover:text-slate-200'}`}>
            {tp(tb.label)}
            {tb.id === 'runs' && pendingReview > 0 && (
              <span className="ms-1.5 px-1.5 py-0.5 rounded-full bg-amber-500/20 text-amber-300 text-[10px]">
                {n(pendingReview)}
              </span>)}
          </button>
        ))}
      </nav>

      {msg && <p className={`text-sm ${msg.kind === 'ok' ? 'text-emerald-400' : 'text-red-400'}`}>{msg.text}</p>}

      {hs?.running && (
        <div className="bg-indigo-500/10 border border-indigo-500/40 rounded-lg p-3 text-[12px] font-mono flex flex-wrap gap-x-5">
          <span className="text-indigo-300">{t('harvesting', 'در حال برداشت')}: {hs.insurer}</span>
          <span>{t('phase', 'مرحله')}: {hs.phase}</span><span>{t('pages', 'صفحات')}: {n(hs.pages)}</span>
          <span>{t('rows', 'ردیف‌ها')}: {n(hs.rows)}</span>
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
          <p className="font-semibold text-sm">{t('Harvest runs', 'اجراهای برداشت')}</p>
          {runList.length === 0 && <p className="text-[12px] text-slate-500">{t('No run has been recorded yet.',
                                                                                'هنوز اجرایی ثبت نشده.')}</p>}
          {runList.map(r => (
            <div key={r.id} className="flex flex-wrap items-center gap-3 text-[12px] border-b border-slate-700/60 pb-1.5">
              <span className="font-mono">{r.insurer}</span>
              <StatusChip status={r.status} />
              <span className="text-slate-500">{r.finished_at ? dateTime(r.finished_at) : '…'}</span>
              {r.stats && <span>{t('rows', 'ردیف')}: {n(r.stats.rows)} · {t('applicable', 'اعمال‌پذیر')}: {n(r.stats.applied)}
                {' '}· {t('review', 'بازبینی')}: {n(r.stats.review)}</span>}
              <span className="text-slate-400">＋{fa(r.diff_counts.added)} / ✎{fa(r.diff_counts.changed)} / −{fa(r.diff_counts.removed)}</span>
              {r.error && <span className="text-red-400 truncate max-w-[24rem]">{r.error}</span>}
              {r.status === 'parsed' &&
                <button onClick={() => setOpenRun(openRun === r.id ? null : r.id)}
                  className="px-2 py-0.5 bg-slate-700 hover:bg-slate-600 rounded">{t('Preview', 'پیش‌نمایش')}</button>}
            </div>
          ))}
          {openRun && <RunPreview runId={openRun} onDone={() => { setOpenRun(null)
            qc.invalidateQueries({ queryKey: ['coverage-runs'] }) }} onError={err} />}
        </div>
      )}

      {tab === 'issues' && (<div className="space-y-5">
        <TriageBoard onError={err} />
        <CountryProposalsPanel onError={err} />
        <details className="bg-slate-800/30 border border-slate-700 rounded-lg">
          <summary className="px-4 py-2 text-sm cursor-pointer text-slate-300">
            🔎 {t('Detailed item list (for case-by-case review)', 'فهرست تفصیلی اقلام (برای بررسی موردی)')}
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
  const { t, tp, dateTime } = useLang()
  const [url, setUrl] = useState(s.url || '')
  const [strategy, setStrategy] = useState(s.strategy)
  const [interval, setIntervalDays] = useState(s.check_interval_days)
  return (
    <div className="bg-slate-800/50 border border-slate-700 rounded-lg p-3 space-y-2">
      <div className="flex items-center gap-2">
        <span className="font-semibold text-sm">{s.name}</span>
        {s.due && <span className="text-[11px] px-2 py-0.5 rounded-full bg-amber-500/15 text-amber-300">{t('Update due', 'به‌روزرسانی لازم')}</span>}
        {s.last_run_status && <StatusChip status={s.last_run_status} />}
      </div>
      <input value={url} onChange={e => setUrl(e.target.value)} dir="ltr" placeholder="https://…"
        className="w-full bg-slate-900 border border-slate-600 rounded px-2 py-1 text-[12px] font-mono" />
      <div className="flex flex-wrap items-center gap-2 text-[12px]">
        <select value={strategy} onChange={e => setStrategy(e.target.value)}
          className="bg-slate-900 border border-slate-600 rounded px-2 py-1">
          {STRATEGIES.map(([v, l]) => <option key={v} value={v}>{tp(l)}</option>)}
        </select>
        <label className="flex items-center gap-1 text-slate-400">{t('every', 'هر')}
          <input type="number" value={interval} onChange={e => setIntervalDays(+e.target.value)}
            className="w-14 bg-slate-900 border border-slate-600 rounded px-1 py-0.5" /> {t('days', 'روز')}</label>
        <button onClick={() => onSave({ url, strategy, check_interval_days: interval })}
          className="px-3 py-1 bg-slate-700 hover:bg-slate-600 rounded">{t('Save', 'ذخیره')}</button>
        <button onClick={onProbe} className="px-3 py-1 bg-indigo-600 hover:bg-indigo-500 rounded">{t('Detect', 'تشخیص')}</button>
        <button onClick={onHarvest} disabled={locked}
          title={locked ? `${t('Harvest lock', 'قفل برداشت')}: ${lockHolder}` : undefined}
          className="px-3 py-1 bg-emerald-600 hover:bg-emerald-500 rounded disabled:opacity-40">{t('Harvest', 'برداشت')}</button>
      </div>
      {s.last_run_at && <p className="text-[11px] text-slate-500">
        {t('Last run', 'آخرین اجرا')}: {dateTime(s.last_run_at)}</p>}
    </div>
  )
}

function ProbePanel({ data, onClose, onSaveOverrides }: {
  data: any; onClose: () => void; onSaveOverrides: (ov: Record<string, string>) => void
}) {
  const { t } = useLang()
  const cols: string[] = data.sample_rows?.[0] ? Object.keys(data.sample_rows[0]) : []
  const [roles, setRoles] = useState<Record<string, string>>(
    () => ({ ...(data.inferred_columns || {}) }))
  return (
    <div className="bg-slate-800/70 border border-indigo-500/40 rounded-lg p-4 space-y-2">
      <div className="flex items-center gap-3">
        <p className="font-semibold text-sm">{t('Detection result', 'نتیجه تشخیص')}</p>
        <span className="text-[11px] px-2 py-0.5 rounded-full bg-indigo-500/15 text-indigo-300">{data.detected_strategy}</span>
        <button onClick={onClose} className="ms-auto text-slate-400 hover:text-slate-200">{t('Close', 'بستن')} ✕</button>
      </div>
      <div className="overflow-x-auto">
        <table className="text-[11px] font-mono">
          <thead><tr>{cols.map(c => (
            <th key={c} className="px-2 py-1 text-start border-b border-slate-600">
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
        className="px-3 py-1 bg-emerald-600 hover:bg-emerald-500 rounded text-sm">{t('Save column settings', 'ذخیره تنظیمات ستون‌ها')}</button>
    </div>
  )
}

function RunPreview({ runId, onDone, onError }: {
  runId: string; onDone: () => void; onError: (e: unknown, f: string) => void
}) {
  const { t, n } = useLang()
  const [removeMissing, setRemoveMissing] = useState(false)
  const [accepted, setAccepted] = useState<Set<number>>(new Set())
  const [reasons, setReasons] = useState<Record<number, { code?: string; note?: string }>>({})
  const [bulkMsg, setBulkMsg] = useState('')
  const [busy, setBusy] = useState(false)
  const { data: run } = useQuery<any>({
    queryKey: ['coverage-run', runId],
    queryFn: () => pricingApi.coverageRun(runId).then(r => r.data),
  })
  if (!run) return <p className="text-[12px] text-slate-500">{t('Loading…', 'در حال بارگذاری…')}</p>
  const decide = async (approve: boolean) => {
    // رد discards the WHOLE run (checked review items are NOT applied) — this
    // was mis-clicked as "apply the checked items" once, hence the guard.
    if (!approve && !window.confirm(
      t('The whole run will be rejected and no coverage applied — not even the approved items. Continue?',
        'کل این اجرا رد می‌شود و هیچ پوششی اعمال نمی‌شود — حتی موارد تأییدشده. ادامه؟'))) return
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
    } catch (e) { onError(e, t('The decision was not applied.', 'تصمیم اعمال نشد.')) } finally { setBusy(false) }
  }
  const proposePrices = async () => {
    setBusy(true)
    try {
      const { data } = await pricingApi.coverageProposePrices(runId,
        { min_confidence: 0.85, min_pct: 25 })
      setBulkMsg(t(
        `${n(data.proposals_created)} price proposals built from ${n(data.qualified)} confident matches `
        + `(↑${n(data.by_kind?.increase || 0)} / ↓${n(data.by_kind?.decrease || 0)}) — review them under “Price proposals”.`,
        `${n(data.proposals_created)} پیشنهاد قیمت از ${n(data.qualified)} تطبیق مطمئن ساخته شد `
        + `(↑${n(data.by_kind?.increase || 0)} / ↓${n(data.by_kind?.decrease || 0)}) — در «پیشنهادهای قیمت» بازبینی کنید.`))
    } catch (e) { onError(e, t('Building price proposals failed.', 'ساخت پیشنهاد قیمت ناموفق بود.')) } finally { setBusy(false) }
  }
  const d = run.diff || { added: 0, changed: 0, removed: 0, samples: {} }
  return (
    <div className="border border-amber-500/40 rounded-lg p-3 space-y-2 text-[12px]">
      <div className="flex flex-wrap gap-4 font-mono">
        <span>{t('rows', 'ردیف‌ها')}: {n(run.stats?.rows)}</span>
        <span className="text-emerald-300">{t('applicable', 'اعمال‌پذیر')}: {n(run.stats?.applied)}</span>
        <span className="text-amber-300">{t('review', 'بازبینی')}: {n(run.stats?.review)}</span>
        <span className="text-red-300">{t('unmatched', 'نامنطبق')}: {n(run.stats?.unmatched)}</span>
        <span>＋{t('new', 'جدید')}: {n(d.added)} · ✎{t('changed', 'تغییر')}: {n(d.changed)}
          {' '}· −{t('dropped from the list', 'حذف‌شده از فهرست')}: {n(d.removed)}</span>
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
            {t(`Error diagnosis — ${n(run.diagnostics.summary.failed)} failed of ${n(run.diagnostics.summary.total)}`,
               `تشخیص خطاها — ${n(run.diagnostics.summary.failed)} ناموفق از ${n(run.diagnostics.summary.total)}`)}
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
                {a.body_snippet && <details><summary className="cursor-pointer text-slate-600">{t('Response body', 'بدنهٔ پاسخ')}</summary>
                  <pre className="whitespace-pre-wrap text-slate-500">{a.body_snippet.slice(0, 500)}</pre></details>}
              </div>))}
          </div>
        </details>)}
      {(run.groups?.bulk_candidates || []).length > 0 && (
        <details className="border border-cyan-600/30 rounded p-2">
          <summary className="cursor-pointer text-cyan-300">
            {t(`Ingredient grouping — ${n(run.stats?.ingredient_groups)} groups · `
               + `${n(run.groups.bulk_candidates.length)} “raw material” candidates`,
               `گروه‌بندی مادهٔ مؤثره — ${n(run.stats?.ingredient_groups)} گروه · `
               + `${n(run.groups.bulk_candidates.length)} نامزد «مادهٔ اولیه»`)}
          </summary>
          <p className="text-[11px] text-slate-500 mt-1">
            {t('These look like pharmaceutical raw materials (no form or strength, alongside finished items of the same substance). Confirming marks them “bulk” and excludes them from drug matching.',
               'این‌ها ماده‌های اولیهٔ داروسازی به‌نظر می‌رسند (بدون شکل/قدرت، کنارِ اقلام نهاییِ همان ماده). تأیید ⇒ به‌عنوان «فله» ثبت و از تطبیق دارو کنار گذاشته می‌شوند.')}
          </p>
          <button onClick={async () => {
            setBusy(true)
            try {
              const { data } = await pricingApi.enrichMarkBulk(run.groups.bulk_candidates)
              setBulkMsg(t(`${n(data.marked)} marked as “raw material” (${n(data.skipped)} skipped).`,
                           `${n(data.marked)} مورد به‌عنوان «مادهٔ اولیه» ثبت شد (${n(data.skipped)} رد شد).`))
            } catch (e) { onError(e, t('Marking as bulk failed.', 'ثبت فله ناموفق بود.')) } finally { setBusy(false) }
          }} disabled={busy}
            className="mt-1 mb-1 px-2 py-0.5 text-[11px] rounded bg-cyan-800 hover:bg-cyan-700 disabled:opacity-40">
            {t('Confirm all as “raw material”', 'تأیید همه به‌عنوان «مادهٔ اولیه»')} ({n(run.groups.bulk_candidates.length)})
          </button>
          {bulkMsg && <span className="text-[11px] text-emerald-400 mr-2">{bulkMsg}</span>}
          <div className="max-h-32 overflow-y-auto mt-1 space-y-0.5 font-mono text-[11px] text-slate-400">
            {run.groups.bulk_candidates.map((name: string, i: number) => (
              <div key={i} className="flex items-center gap-2">
                <button onClick={async () => {
                  try { await pricingApi.enrichMarkBulk([name]) } catch (e) { onError(e, t('Marking as bulk failed.', 'ثبت فله ناموفق بود.')) }
                }} className="text-cyan-400 hover:text-cyan-200"
                   title={t('Mark this one as a raw material', 'ثبت این مورد به‌عنوان مادهٔ اولیه')}>{t('bulk', 'فله')} ✓</button>
                <span>{name}</span>
              </div>))}
          </div>
        </details>)}
      {(run.review || []).length > 0 && (
        <div className="max-h-40 overflow-y-auto space-y-0.5">
          <div className="flex flex-wrap items-center gap-2">
            <p className="font-semibold">{t('Items needing review — confirming one applies it with the run:',
                                                'موارد نیازمند بازبینی — تأیید هر مورد آن را همراه اجرا اعمال می‌کند:')}</p>
            <button onClick={() => setAccepted(new Set((run.review || []).map((i: any) => i.id)))}
              className="px-2 py-0.5 bg-slate-700 hover:bg-slate-600 rounded text-[11px]">
              {t('Confirm all', 'تأیید همه')} ({n((run.review || []).length)})
            </button>
            <button onClick={() => setAccepted(new Set())}
              className="px-2 py-0.5 bg-slate-700 hover:bg-slate-600 rounded text-[11px]">{t('Clear all', 'لغو همه')}</button>
          </div>
          {[...run.review].sort((a: any, b: any) =>
            Math.abs(a.fs ?? 999) - Math.abs(b.fs ?? 999)   // active learning: boundary cases first
          ).map((item: any) => (
            <div key={item.id} className="flex flex-wrap items-center gap-2 font-mono text-slate-400">
              <label className="flex items-center gap-2">
                <input type="checkbox" checked={accepted.has(item.id)}
                  onChange={e => { const s = new Set(accepted); e.target.checked ? s.add(item.id) : s.delete(item.id); setAccepted(s) }} />
                {item.name} ← «{String(item.row?.drug_name ?? '')}» ({t('confidence', 'اطمینان')} {item.confidence}
                {item.fs != null && <span title={t('Match-intelligence score', 'امتیاز هوش تطبیق')}>{' '}· FS {item.fs}</span>})
              </label>
              {!accepted.has(item.id) && (
                <span className="flex items-center gap-1">
                  <select value={reasons[item.id]?.code || ''}
                    onChange={e => setReasons(r => ({ ...r, [item.id]: { ...r[item.id], code: e.target.value || undefined } }))}
                    className="bg-slate-900 border border-slate-700 rounded px-1 py-0.5 text-[11px]">
                    <option value="">{t('Rejection reason…', 'دلیل رد…')}</option>
                    <option value="wrong_product">{t('Different drug', 'داروی دیگر')}</option>
                    <option value="wrong_strength">{t('Wrong strength', 'قدرت اشتباه')}</option>
                    <option value="wrong_form">{t('Wrong dosage form', 'شکل اشتباه')}</option>
                    <option value="wrong_brand">{t('Wrong brand', 'برند اشتباه')}</option>
                    <option value="wrong_pack">{t('Wrong pack', 'بستهٔ اشتباه')}</option>
                    <option value="price_implausible">{t('Implausible price', 'قیمت نامعقول')}</option>
                    <option value="other">{t('Other', 'سایر')}</option>
                  </select>
                  <input type="text" placeholder={t('Note (optional)', 'توضیح (اختیاری)')}
                    value={reasons[item.id]?.note || ''}
                    onChange={e => setReasons(r => ({ ...r, [item.id]: { ...r[item.id], note: e.target.value || undefined } }))}
                    className="bg-slate-900 border border-slate-700 rounded px-1.5 py-0.5 text-[11px] w-40" />
                </span>
              )}
            </div>))}
        </div>)}
      <label className="flex items-center gap-2 text-amber-300">
        <input type="checkbox" checked={removeMissing} onChange={e => setRemoveMissing(e.target.checked)} />
        {t('Remove coverage for items absent from the new list (up to 50 sampled — use with care)',
           'حذف پوشش اقلامی که در فهرست جدید نیستند (حداکثر ۵۰ مورد نمونه‌گیری‌شده — با احتیاط)')}
      </label>
      <div className="flex items-center gap-2">
        <button onClick={() => decide(true)} disabled={busy}
          className="px-4 py-1.5 bg-emerald-600 hover:bg-emerald-500 rounded disabled:opacity-50">
          {t('Apply run', 'اعمال اجرا')}{accepted.size > 0
            ? t(` (+${n(accepted.size)} confirmed)`, ` (+${n(accepted.size)} مورد تأییدشده)`) : ''}
        </button>
        <button onClick={() => decide(false)} disabled={busy}
          className="px-4 py-1.5 bg-red-600/70 hover:bg-red-500 rounded disabled:opacity-50">{t('Reject the whole run', 'رد کل اجرا')}</button>
        <button onClick={proposePrices} disabled={busy}
          title={t('Turn this insurer\u2019s current prices into price-update proposals for the confident matches (reviewed separately)',
                   'قیمت‌های جاری این بیمه‌گر را برای تطبیق‌های مطمئن به‌عنوان پیشنهاد به‌روزرسانی قیمت بساز (بازبینی جداگانه)')}
          className="px-4 py-1.5 bg-cyan-700 hover:bg-cyan-600 rounded disabled:opacity-50">💰 {t('Update prices from this run', 'به‌روزرسانی قیمت از این اجرا')}</button>
        <span className="text-[11px] text-slate-500">{t('Confirmed items are applied only together with “Apply run”.',
                                                        'موارد تأییدشده فقط همراه «اعمال اجرا» اعمال می‌شوند.')}</span>
      </div>
    </div>
  )
}

const UPLOAD_PHASE: Record<string, Pair> = {
  starting: ['Preparing…', 'در حال آماده‌سازی…'], fetching: ['Fetching…', 'در حال دریافت…'],
  linking: ['Matching against the catalog…', 'در حال تطبیق با کاتالوگ…'],
  diffing: ['Comparing with current coverage…', 'در حال مقایسه با پوشش فعلی…'],
  saving: ['Saving the run…', 'در حال ذخیرهٔ اجرا…'],
  done: ['Done', 'انجام شد'], failed: ['Failed', 'ناموفق'],
}

function UploadCard({ onMsg }: { onMsg: (m: { kind: 'ok' | 'err'; text: string }) => void }) {
  const { t, tp, n } = useLang()
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
      onMsg({ kind: 'ok', text: t('Processing complete — the new run is ready for review under “Runs”.',
                                  'پردازش کامل شد — اجرای جدید در تب «اجراها» آمادهٔ بازبینی است.') })
    } else if (hs.error) {
      onMsg({ kind: 'err', text: t(`Processing failed: ${hs.error}`, `پردازش ناموفق: ${hs.error}`) })
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
      onMsg({ kind: 'ok', text: t(`${n(data.merged_rows)} rows extracted and merged — processing started in the background.`,
                                  `${n(data.merged_rows)} ردیف استخراج و ادغام شد — پردازش در پس‌زمینه آغاز شد.`) })
      setFiles([]); if (covRef.current) covRef.current.value = ''
    } catch (e: unknown) {
      onMsg({ kind: 'err', text: apiErrorText(e, t('Uploading the formulary failed.', 'بارگذاری دارونامه ناموفق بود.')) })
    } finally { setBusy(false) }
  }

  const working = busy || (tracking && !!hs?.running)
  return (
    <div className="bg-slate-800/50 border border-slate-700 rounded-lg p-4 space-y-2">
      <p className="font-semibold text-sm">{t('Manual formulary upload (staged run)', 'بارگذاری دستی دارونامه (اجرای مرحله‌ای)')}</p>
      <p className="text-[11px] text-slate-500">
        {t('Excel/CSV/JSON or a saved HTML page — several files of one formulary (Tamin’s .json and .csv, say) are merged on the drug code. The result appears as a reviewable run under “Runs”.',
           'Excel/CSV/JSON یا صفحه HTML ذخیره‌شده — چند فایل از یک دارونامه (مثلاً ‎.json و ‎.csv تأمین) با کد دارو ادغام می‌شوند. نتیجه به‌صورت اجرای قابل بازبینی در تب «اجراها» ظاهر می‌شود.')}
      </p>
      <div className="flex flex-wrap items-center gap-3 text-sm">
        <select value={covInsurer} onChange={e => setCovInsurer(e.target.value)} disabled={working}
          className="bg-slate-900 border border-slate-600 rounded px-2 py-1">
          <option value="tamin">{t('Social Security (Tamin)', 'تأمین اجتماعی')}</option>
          <option value="salamat">{t('Salamat Insurance', 'بیمه سلامت')}</option>
          <option value="armed_forces">{t('Armed Forces', 'نیروهای مسلح')}</option>
        </select>
        <input ref={covRef} type="file" multiple accept=".xlsx,.xls,.csv,.tsv,.html,.htm,.json"
          className="text-sm text-slate-300" disabled={working}
          onChange={e => setFiles(Array.from(e.target.files || []))} />
        <button onClick={start} disabled={working || !files.length}
          className="px-4 py-1.5 bg-emerald-600 hover:bg-emerald-500 rounded disabled:opacity-50">
          🚀 {t('Upload and process', 'بارگذاری و پردازش')}
        </button>
      </div>
      {files.length > 0 && !working && (
        <ul className="text-[11px] text-slate-400 space-y-0.5">
          {files.map(f => <li key={f.name}>📄 {f.name} — {fa(Math.max(1, Math.round(f.size / 1024)))} KB</li>)}
        </ul>)}
      {working && (
        <div className="flex items-center gap-2 text-xs text-cyan-300">
          <span className="inline-block w-3 h-3 border-2 border-cyan-400 border-t-transparent rounded-full animate-spin" />
          <span>{busy ? t('Uploading file(s)…', 'در حال بارگذاری فایل(ها)…')
                      : (UPLOAD_PHASE[hs?.phase || ''] ? tp(UPLOAD_PHASE[hs!.phase]) : hs?.phase)}{' '}
            {!busy && hs?.rows ? t(`· ${n(hs.rows)} rows`, `· ${n(hs.rows)} ردیف`) : ''}</span>
        </div>)}
      {fileStats && (
        <div className="text-[11px] text-slate-400">
          {fileStats.map(s => <span key={s.file} className="me-3">📄 {s.file}: {t(`${n(s.rows)} rows`, `${n(s.rows)} ردیف`)}</span>)}
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

// Insurer names stay in Persian in both languages: they are the organisations'
// own registered names, and staff read them off the insurers' own paperwork.
const INSURERS = [['tamin', 'تأمین اجتماعی'], ['salamat', 'بیمه سلامت'],
                  ['armed_forces', 'نیروهای مسلح']] as const

function Badge({ n, active }: { n: number; active?: boolean }) {
  const tone = n === 0 ? 'bg-emerald-500/15 text-emerald-300'
             : active ? 'bg-slate-900/40 text-slate-100' : 'bg-slate-700 text-slate-200'
  return <span className={`text-xs px-2 py-0.5 rounded-full tabular-nums ${tone}`}>{fa(n)}</span>
}

function Empty({ text }: { text?: string }) {
  const { t } = useLang()
  text ??= t('No inconsistency found ✓', 'هیچ ناسازگاری‌ای یافت نشد ✓')
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
  ['price', ['Price mismatch', 'مغایرت قیمت']], ['review', ['Needs review', 'نیازمند بازبینی']],
  ['unmatched', ['Unmatched', 'نامنطبق']], ['no_price', ['No price', 'بدون قیمت']],
  ['no_generic', ['No generic', 'بدون ژنریک']], ['no_country', ['No country', 'بدون کشور']],
  ['no_atc', ['No ATC', 'بدون ATC']],
] as const satisfies ReadonlyArray<readonly [string, Pair]>
type IssueType = (typeof ISSUE_TYPES)[number][0]
const ISSUE_LABEL = Object.fromEntries(ISSUE_TYPES) as Record<IssueType, Pair>

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
  const { t, n } = useLang()
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
      alert(t(`Repaired: ${n(res.repaired)} items — the repairs are recorded as permanent overrides.`,
              `اصلاح شد: ${n(res.repaired)} قلم — اصلاح‌ها به‌صورت override دائمی ثبت شدند.`))
    } catch (e) { onError(e, t('Applying the repair failed.', 'اعمال اصلاح ناموفق بود.')) } finally { setBusy(false) }
  }
  if (!suspects.length) return null
  return (
    <div className="bg-rose-950/30 border border-rose-500/40 rounded-lg p-3 space-y-2">
      <button onClick={() => setOpen(o => !o)} className="w-full flex items-center justify-between">
        <span className="font-semibold text-sm text-rose-200">
          🧬 {t(`NFI internal inconsistency — spliced pages (${n(suspects.length)} suspect of ${n(data!.counts.checked)})`,
                `مغایرت داخلی NFI — صفحات دوپاره (${n(suspects.length)} قلم مشکوک از ${n(data!.counts.checked)})`)}
        </span>
        <span className="text-rose-300 text-xs">{open ? `▲ ${t('close', 'بستن')}` : `▼ ${t('show', 'نمایش')}`}</span>
      </button>
      {open && (<>
        <p className="text-[11px] text-rose-200/70 leading-5">
          {t('Legacy NFI pages sometimes show another drug’s monograph (a reused generic-entity id) — the name, price and manufacturer belong to this item, but the generic, form and ATC belong to a different drug. The proposed repair is built from a healthy sibling of the same product (🎯) or from the brand name itself. An approved repair is stored as a permanent override, so the next crawl cannot break it again.',
             'صفحات قدیمی سایت NFI گاهی مونوگرافِ دارویی دیگر را نشان می‌دهند (شناسهٔ ژنریکِ بازاستفاده‌شده) — نام/قیمت/تولیدکننده متعلق به خود قلم است ولی ژنریک/شکل/ATC متعلق به داروی دیگری. پیشنهادِ اصلاح از روی همتای سالمِ همان محصول (🎯) یا خودِ نام برند ساخته شده است. اصلاح تأییدشده به‌صورت override دائمی ثبت می‌شود و خزش بعدی نمی‌تواند آن را دوباره خراب کند.')}
        </p>
        <div className="flex items-center gap-2">
          <button disabled={busy} onClick={() => setSel(new Set(suspects.map(s => s.irc)))}
            className="px-2 py-1 text-xs bg-slate-700 hover:bg-slate-600 rounded">{t('Select all', 'انتخاب همه')}</button>
          <button disabled={busy} onClick={() => setSel(new Set(suspects.filter(s => s.donor_irc).map(s => s.irc)))}
            className="px-2 py-1 text-xs bg-slate-700 hover:bg-slate-600 rounded"
            title={t('Only those with a healthy sibling of the same product', 'فقط مواردی که همتای سالم همان محصول پیدا شده')}>
            🎯 {t('Select those with a sibling', 'انتخاب موارد با همتا')} ({n(data!.counts.with_donor)})</button>
          <button disabled={busy || !sel.size} onClick={() => apply([...sel])}
            className="px-3 py-1 text-xs bg-emerald-600 hover:bg-emerald-500 rounded disabled:opacity-50">
            ✔ {t(`Repair ${n(sel.size)} selected`, `اصلاح ${n(sel.size)} مورد انتخاب‌شده`)}</button>
        </div>
        <div className="overflow-x-auto max-h-96 overflow-y-auto">
          <table className="w-full text-[11px]">
            <thead className="sticky top-0 bg-slate-900">
              <tr className="text-slate-400 text-right">
                <th className="p-1.5"></th><th className="p-1.5">{t('Brand (product block)', 'برند (بلوک محصول)')}</th>
                <th className="p-1.5">{t('Current monograph (broken)', 'مونوگراف فعلی (خراب)')}</th>
                <th className="p-1.5">{t('Proposed repair', 'پیشنهاد اصلاح')}</th>
                <th className="p-1.5">{t('Source', 'منبع')}</th>
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
                      {s.announced_price != null && <> · {t(`${n(s.announced_price)} IRR`, `${n(s.announced_price)} ریال`)}</>}</div>
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
                    {s.donor_irc
                      ? <span title={`${t('sibling IRC', 'IRC همتا')}: ${s.donor_irc}`}>🎯 {t('healthy sibling', 'همتای سالم')}</span>
                      : `📛 ${t('from the brand name', 'از نام برند')}`}
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
  // English readings come from the same server-side catalogue (issue_registry
  // .CAUSES_EN), so the board never carries a second copy of this vocabulary
  lane_en?: string; title_en?: string; why_en?: string; action_en?: string
}
interface Board {
  causes: Cause[]
  totals: { open: number; acknowledged: number; all: number; by_lane: Record<string, number> }
  lanes: Record<string, string>
  lanes_en?: Record<string, string>
}

const LANE_STYLE: Record<string, string> = {
  auto:      'border-emerald-500/40 bg-emerald-500/5',
  bulk:      'border-cyan-500/40 bg-cyan-500/5',
  research:  'border-violet-500/40 bg-violet-500/5',
  judgement: 'border-amber-500/40 bg-amber-500/5',
  expected:  'border-slate-500/40 bg-slate-500/5',
  blocked:   'border-rose-500/40 bg-rose-500/5',
}
const ROUTE_HINT: Record<string, Pair> = {
  nfi_integrity: ['NFI catalog → internal inconsistency', 'کاتالوگ NFI → مغایرت داخلی'],
  run_review: ['Runs → review the run', 'اجراها → بازبینی اجرا'],
  enrichment: ['✨ Enrichment', '✨ غنی‌سازی'],
  nfi_harvest: ['NFI catalog → start harvest (needs a proxy)', 'کاتالوگ NFI → شروع برداشت (نیازمند پروکسی)'],
  price_review: ['📉 Prices', '📉 قیمت‌ها'],
  acknowledge: ['Right here — acknowledge as a group', 'همین‌جا — پذیرش گروهی'],
}
const DISPOSITION_LABEL: Record<string, Pair> = {
  accepted: ['Accepted', 'پذیرفته‌شده'], wont_fix: ['Will not fix', 'رفع نمی‌شود'],
  resolved: ['Resolved', 'رفع‌شده'], deferred: ['Deferred', 'به تعویق'],
}

function TriageBoard({ onError }: { onError: (e: unknown, f: string) => void }) {
  const { t, tp, n, lang } = useLang()
  const en = lang === 'en'
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
    } catch (e) { onError(e, t('The ruling could not be recorded.', 'ثبت تصمیم ناموفق بود.')) } finally { setBusy(null) }
  }
  const reopen = async (cause: string) => {
    setBusy(cause)
    try {
      await pricingApi.issuesRulingClear(cause)
      qc.invalidateQueries({ queryKey: ['issues-board'] })
    } catch (e) { onError(e, t('Reopening failed.', 'بازگشایی ناموفق بود.')) } finally { setBusy(null) }
  }
  const tot = data?.totals
  const pct = tot && tot.all ? Math.round(100 * tot.acknowledged / tot.all) : 0

  return (
    <div className="space-y-4">
      {/* burn-down header: the number must be able to go DOWN */}
      <div className="bg-slate-800/50 border border-slate-700 rounded-lg p-4 space-y-3">
        <div className="flex flex-wrap items-baseline gap-x-6 gap-y-1">
          <span className="text-sm font-semibold">{t('Inconsistency triage board', 'تابلوی مدیریت ناسازگاری‌ها')}</span>
          <span className="text-2xl font-mono text-amber-300">{n(tot?.open ?? 0)}</span>
          <span className="text-[11px] text-slate-400">{t('open', 'باز')}</span>
          <span className="text-lg font-mono text-emerald-300">{n(tot?.acknowledged ?? 0)}</span>
          <span className="text-[11px] text-slate-400">{t('decided', 'تصمیم‌گرفته')} ({n(pct)}{t('%', '٪')})</span>
          {isFetching && <span className="text-[11px] text-cyan-300">{t('calculating…', 'در حال محاسبه…')}</span>}
        </div>
        <div className="h-2 bg-slate-700 rounded-full overflow-hidden">
          <div className="h-full bg-emerald-500 transition-all" style={{ width: `${pct}%` }} />
        </div>
        <p className="text-[11px] text-slate-500">
          {t('Inconsistencies are grouped by root cause, not row by row: one ruling closes the whole group and stops it recurring in later runs.',
             'ناسازگاری‌ها بر پایهٔ «علت ریشه‌ای» گروه‌بندی شده‌اند، نه ردیف‌به‌ردیف: یک تصمیم، کل گروه را می‌بندد و در اجراهای بعدی دیگر تکرار نمی‌شود.')}
        </p>
        <div className="flex flex-wrap gap-2 text-[11px]">
          {Object.entries(tot?.by_lane || {}).sort((a, b) => b[1] - a[1]).map(([lane, count]) => (
            <span key={lane} className={`px-2 py-0.5 rounded border ${LANE_STYLE[lane] || ''}`}>
              {(en && data?.lanes_en?.[lane]) || data?.lanes[lane]}: {n(count)}
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
                <div className="text-sm font-semibold">{(en && c.title_en) || c.title}</div>
                <div className="text-[11px] text-slate-400">{(en && c.lane_en) || c.lane_fa}</div>
              </div>
              <div className="text-xl font-mono shrink-0">{n(c.count)}</div>
            </div>
            <p className="text-[11px] text-slate-400 leading-relaxed">{(en && c.why_en) || c.why}</p>
            <p className="text-[11px] text-cyan-300 leading-relaxed">◆ {(en && c.action_en) || c.action}</p>
            <p className="text-[10px] text-slate-500">{t('Goes to', 'مقصد')}: {
              ROUTE_HINT[c.route] ? tp(ROUTE_HINT[c.route]) : c.route}</p>
            {c.closed ? (
              <div className="flex items-center gap-2 text-[11px]">
                <span className="text-emerald-300">
                  ✓ {DISPOSITION_LABEL[c.disposition || 'deferred']
                       ? tp(DISPOSITION_LABEL[c.disposition || 'deferred']) : c.disposition}
                  {c.reason ? ` — ${c.reason}` : ''}
                </span>
                <button onClick={() => reopen(c.cause)} disabled={busy === c.cause}
                  className="px-2 py-0.5 bg-slate-700 hover:bg-slate-600 rounded disabled:opacity-50">
                  {t('Reopen', 'بازگشایی')}</button>
              </div>
            ) : (
              <div className="flex flex-wrap gap-2 text-[11px]">
                {/* The stored reason is the audit record — it stays Persian so a
                    ruling reads the same to whoever opens the ledger later. */}
                <button onClick={() => rule(c.cause, 'accepted', 'طبیعی و مورد انتظار')}
                  disabled={busy === c.cause}
                  className="px-2 py-1 bg-emerald-700 hover:bg-emerald-600 rounded disabled:opacity-50">
                  {t('Acknowledge the group', 'پذیرش گروهی')}</button>
                <button onClick={() => rule(c.cause, 'deferred', 'در انتظار پیش‌نیاز')}
                  disabled={busy === c.cause}
                  className="px-2 py-1 bg-slate-700 hover:bg-slate-600 rounded disabled:opacity-50">
                  {t('Defer', 'تعویق')}</button>
                <button onClick={() => rule(c.cause, 'wont_fix', 'رفع نمی‌شود')}
                  disabled={busy === c.cause}
                  className="px-2 py-1 bg-slate-700 hover:bg-slate-600 rounded disabled:opacity-50">
                  {t('Will not fix', 'رفع نمی‌شود')}</button>
              </div>
            )}
          </div>))}
      </div>
    </div>
  )
}

interface CountryProposal {
  irc: string; name_fa: string; generic_name: string | null; manufacturer: string
  proposed_country: string; confidence: number; evidence: string
}

function CountryProposalsPanel({ onError }: { onError: (e: unknown, f: string) => void }) {
  const { t, n } = useLang()
  const qc = useQueryClient()
  const [floor, setFloor] = useState(0.95)
  const [busy, setBusy] = useState(false)
  const [done, setDone] = useState<number | null>(null)
  const { data } = useQuery<{ proposals: CountryProposal[]; total: number
                              firms: Record<string, number> }>({
    queryKey: ['country-proposals'],
    queryFn: () => pricingApi.countryProposals(0).then(r => r.data),
  })
  const all = data?.proposals || []
  const eligible = all.filter(p => p.confidence >= floor)
  const applyBulk = async () => {
    setBusy(true); setDone(null)
    try {
      const { data: r } = await pricingApi.countryProposalsApply({ min_confidence: floor })
      setDone(r.applied)
      qc.invalidateQueries({ queryKey: ['country-proposals'] })
    } catch (e) { onError(e, t('Applying the countries failed.', 'اعمال کشورها ناموفق بود.')) } finally { setBusy(false) }
  }
  if (!all.length) return null
  return (
    <div className="bg-slate-800/50 border border-slate-700 rounded-lg p-4 space-y-3">
      <div className="flex flex-wrap items-baseline gap-x-4 gap-y-1">
        <span className="font-semibold text-sm">🌍 {t('Manufacturing-country proposals', 'پیشنهاد کشور سازنده')}</span>
        <span className="text-[11px] text-slate-400">
          {t('The NFI page states no country; inferred from the manufacturer’s nationality — importer firms are excluded',
             'صفحهٔ NFI کشور را اعلام نکرده؛ استنتاج از ملیت سازنده — واردکنندگان کنار گذاشته شده‌اند')}
        </span>
      </div>
      <div className="flex flex-wrap items-center gap-3 text-sm">
        <label className="text-[11px] text-slate-400">{t('Minimum confidence:', 'حداقل اطمینان:')}</label>
        {[1, 0.95, 0.9, 0.7].map(f => (
          <button key={f} onClick={() => setFloor(f)}
            className={`px-2 py-0.5 rounded text-[11px] border ${floor === f
              ? 'border-emerald-500 bg-emerald-500/15 text-emerald-300'
              : 'border-slate-600 text-slate-400'}`}>
            ≥{n(Math.round(f * 100))}{t('%', '٪')}
          </button>))}
        <span className="text-[11px] text-slate-300">
          {t(`${n(eligible.length)} of ${n(all.length)} eligible`, `${n(eligible.length)} از ${n(all.length)} مورد واجد شرایط`)}
        </span>
        <button onClick={applyBulk} disabled={busy || !eligible.length}
          className="px-3 py-1 bg-emerald-700 hover:bg-emerald-600 rounded text-[11px] disabled:opacity-50">
          {t(`Apply ${n(eligible.length)}`, `اعمال ${n(eligible.length)} مورد`)}
        </button>
        {done != null && (
          <span className="text-[11px] text-emerald-300">✓ {t(`${n(done)} countries applied`, `${n(done)} کشور اعمال شد`)}</span>)}
      </div>
      <div className="max-h-64 overflow-auto">
        <table className="w-full text-[11px]">
          <tbody>
            {eligible.slice(0, 60).map(p => (
              <tr key={p.irc} className="border-t border-slate-700/50">
                <td className="py-1 pe-2 text-start">{p.name_fa}</td>
                <td className="py-1 text-slate-400">{p.manufacturer}</td>
                <td className="py-1 text-slate-400" dir="auto">{p.evidence}</td>
                <td className={`py-1 text-left tabular-nums ${p.confidence >= 0.95
                  ? 'text-emerald-300' : p.confidence >= 0.9 ? 'text-amber-300' : 'text-rose-300'}`}>
                  {fa(Math.round(p.confidence * 100))}٪
                </td>
              </tr>))}
          </tbody>
        </table>
      </div>
    </div>
  )
}

function InconsistenciesPanel({ onError }: { onError: (e: unknown, f: string) => void }) {
  const { t, tp, n, dir } = useLang()
  const [insurer, setInsurer] = useState('salamat')
  const [threshold, setThreshold] = useState(25)
  const [search, setSearch] = useState('')
  const [activeChips, setActiveChips] = useState<Set<IssueType>>(new Set())
  const [selectedIrc, setSelectedIrc] = useState<string | null>(null)

  const { data, isFetching, isError, error } = useQuery<IncData>({
    queryKey: ['inconsistencies', insurer, threshold],
    queryFn: () => pricingApi.inconsistencies(insurer, threshold).then(r => r.data),
  })
  if (isError) onError(error, t('Fetching inconsistencies failed.', 'دریافت ناسازگاری‌ها ناموفق بود.'))

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
      const f = at(`unmatched#${i}`, text)
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
    <div className="bg-slate-800/50 border border-indigo-500/30 rounded-lg p-4 space-y-4" dir={dir}>
      <NfiIntegrityCard onError={onError} />
      {/* controls */}
      <div className="flex flex-wrap items-center gap-3">
        <label className="flex items-center gap-1.5 text-sm text-slate-400">{t('Insurer', 'بیمه‌گر')}
          <select value={insurer} onChange={e => setInsurer(e.target.value)}
            className="bg-slate-900 border border-slate-600 rounded px-2 py-1 text-slate-100">
            {INSURERS.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
          </select>
        </label>
        <label className="flex items-center gap-1.5 text-sm text-slate-400">{t('Price-gap threshold (%)', 'آستانهٔ اختلاف قیمت (٪)')}
          <input type="number" min={0} value={threshold}
            onChange={e => setThreshold(Math.max(0, +e.target.value))}
            className="w-20 bg-slate-900 border border-slate-600 rounded px-2 py-1 text-slate-100 tabular-nums" />
        </label>
        <input type="search" value={search} onChange={e => setSearch(e.target.value)}
          placeholder={t('Search (IRC or name)…', 'جست‌وجو (IRC یا نام)…')}
          className="flex-1 min-w-[12rem] bg-slate-900 border border-slate-600 rounded px-3 py-1 text-sm text-slate-100 placeholder:text-slate-500" />
        {isFetching && <span className="text-xs text-indigo-300">{t('Loading…', 'در حال بارگذاری…')}</span>}
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
              {tp(label)}<span className="tabular-nums opacity-80">{n(chipCounts[key])}</span>
            </button>)
        })}
        {activeChips.size > 0 && (
          <button onClick={() => setActiveChips(new Set())}
            className="text-xs text-slate-400 hover:text-slate-200 underline underline-offset-2">{t('Clear filters', 'پاک‌کردن فیلترها')}</button>)}
      </div>

      {/* summary tiles */}
      <div className="flex flex-wrap gap-3">
        <div className="flex items-center gap-2 bg-slate-900/40 border border-slate-700 rounded-md px-3 py-2">
          <span className="text-xs text-slate-400">{t('Flagged items', 'اقلام پرچم‌دار')}</span>
          <span className="text-lg font-semibold tabular-nums text-slate-100">{n(flagged.length)}</span>
        </div>
        <div className="flex items-center gap-2 bg-slate-900/40 border border-slate-700 rounded-md px-3 py-2">
          <span className="text-xs text-slate-400">{t('Unmatched (not found in the catalog)', 'نامنطبق (در کاتالوگ یافت نشد)')}</span>
          <span className="text-lg font-semibold tabular-nums text-slate-100">{n(data?.coverage.counts.unmatched ?? 0)}</span>
        </div>
      </div>

      {/* workbench: unified list (right/start) + detail drawer (left/end on lg) */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4 items-start">
        {/* unified flagged-drug list */}
        <div>
          {!data ? <p className="text-sm text-slate-500 py-4">{t('Loading…', 'در حال بارگذاری…')}</p>
            : shown.length === 0 ? (
              <Empty text={flagged.length === 0
                ? t('No flagged item found ✓', 'هیچ قلم پرچم‌داری یافت نشد ✓')
                : t('Nothing matches these filters.', 'موردی با این فیلترها یافت نشد.')} />
            ) : (
              <ScrollTable head={
                <tr><th className="px-3 py-2 text-start font-medium">{t('Formulary item', 'قلم دارونامه')}</th>
                    <th className="px-3 py-2 text-start font-medium">{t('Conflicting target', 'مورد ناسازگار هدف')}</th>
                    <th className="px-3 py-2 text-end font-medium">{t('Price', 'قیمت')}</th></tr>}>
                {shown.map(f => {
                  const unmatchedOnly = f.issues.has('unmatched')
                  return (
                  <tr key={f.irc} onClick={() => !unmatchedOnly && setSelectedIrc(f.irc)}
                    className={`transition-colors ${unmatchedOnly ? '' : 'cursor-pointer'} ${
                      selectedIrc === f.irc ? 'bg-indigo-500/15' : 'hover:bg-slate-700/25'}`}>
                    {/* 1 — what the insurer's formulary actually says */}
                    <td className="px-3 py-2 text-start align-top">
                      <div className="text-slate-200">{f.formulary || f.name}</div>
                      <span className="flex flex-wrap gap-1 mt-1">
                        {[...f.issues].map(iss => (
                          <span key={iss} className={`text-[10px] px-1.5 py-0.5 rounded border ${issueTone(iss, f.gap)}`}>
                            {tp(ISSUE_LABEL[iss])}</span>))}
                      </span>
                    </td>
                    {/* 2 — the catalog product it conflicts with (or none) */}
                    <td className="px-3 py-2 text-start align-top">
                      {unmatchedOnly ? (
                        <span className="text-slate-500">{t('Not found in the catalog', 'در کاتالوگ یافت نشد')}</span>
                      ) : (<>
                        <div className="text-slate-200">{f.name}</div>
                        <div className="text-[11px] font-mono text-slate-500 tabular-nums">{f.irc}</div>
                      </>)}
                    </td>
                    {/* 3 — the money: announced vs insurer reference, and the gap */}
                    <td className="px-3 py-2 text-end align-top whitespace-nowrap tabular-nums">
                      <div className="text-slate-300">
                        {f.announced != null && <span title={t('Catalog announced price', 'قیمت اعلامی کاتالوگ')}>{n(f.announced)}</span>}
                        {f.announced != null && f.reference != null && <span className="text-slate-600"> → </span>}
                        {f.reference != null && <span title={t('Insurer reference price', 'قیمت مرجع بیمه‌گر')}>{n(f.reference)}</span>}
                        {f.announced == null && f.reference == null && <span className="text-slate-600">—</span>}
                      </div>
                      {f.gap != null && (
                        <div className={`text-[11px] font-semibold ${gapTone(f.gap)}`}>
                          {f.gap > 0 ? '+' : ''}{n(f.gap)}{t('%', '٪')}
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
                {t('Pick an item from the list to see its catalog record, insurance coverage, same-molecule siblings and analysis.',
                   'یک قلم را از فهرست انتخاب کنید تا کاتالوگ، پوشش بیمه، هم‌مولکول‌ها و تحلیل آن نمایش داده شود.')}
              </div>}
        </div>
      </div>

      {/* unmatched — no IRC, cannot join; separate section */}
      <section className="space-y-2">
        <div className="flex items-center gap-2">
          <h4 className="text-sm font-semibold text-slate-200">{t('Not found in the catalog (unmatched)', 'در کاتالوگ یافت نشد (نامنطبق)')}</h4>
          <Badge n={unmatched.length} />
        </div>
        {unmatched.length === 0
          ? <Empty text={t('Every formulary row was matched ✓', 'همهٔ ردیف‌های دارونامه تطبیق داده شدند ✓')} />
          : <ScrollTable head={
              <tr><th className="px-3 py-2 text-start font-medium">{t('Name in the formulary', 'نام در دارونامه')}</th>
                  <th className="px-3 py-2 text-end font-medium">{t('Confidence', 'اطمینان')}</th></tr>}>
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
  const { t, n } = useLang()
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
          className="text-xs text-slate-400 hover:text-slate-100 border border-slate-600 rounded px-2 py-1">{t('Close', 'بستن')} ✕</button>
      </div>

      {isFetching && !detail ? <p className="text-sm text-slate-500 py-4">{t('Loading…', 'در حال بارگذاری…')}</p>
        : isError ? <Empty text={t('Fetching the item details failed.', 'دریافت جزئیات قلم ناموفق بود.')} />
        : !detail ? null : (
        <div className="space-y-4">
          {/* کاتالوگ NFI */}
          <section className="space-y-1.5">
            <h5 className="text-xs font-semibold text-slate-300 uppercase tracking-wide">{t('NFI catalog', 'کاتالوگ NFI')}</h5>
            {!cat ? <Empty text={t('This IRC is not present in the NFI catalog.', 'این IRC در کاتالوگ NFI موجود نیست.')} />
              : <div className="grid grid-cols-1 sm:grid-cols-2 gap-x-6 text-sm bg-slate-800/40 rounded-md p-3">
                  {catField(t('Latin name', 'نام لاتین'), cat.name_en)}
                  {catField(t('Generic', 'ژنریک'), cat.generic_name)}
                  {catField(t('Strength', 'قدرت'), cat.strength)}
                  {catField(t('Dosage form', 'شکل دارویی'), cat.dosage_form)}
                  {catField(t('Brand', 'برند'), cat.brand_name)}
                  {catField(t('Manufacturer', 'سازنده'), cat.manufacturer)}
                  {catField(t('Country', 'کشور'), cat.country)}
                  {catField('ATC', cat.atc)}
                  {catField(t('Announced price', 'قیمت اعلامی'), cat.announced_price == null ? null : n(cat.announced_price))}
                  {catField(t('Units per pack', 'تعداد در بسته'), cat.package_count)}
                  {catField('GTIN', cat.gtin)}
                  {catField(t('Source', 'منبع'), cat.source)}
                </div>}
          </section>

          {/* پوشش بیمه — a row per insurer present */}
          <section className="space-y-1.5">
            <h5 className="text-xs font-semibold text-slate-300 uppercase tracking-wide">{t('Insurance coverage', 'پوشش بیمه')}</h5>
            {Object.keys(detail.coverage).length === 0 ? <Empty text={t('No coverage row is recorded for this item.', 'هیچ ردیف پوششی برای این قلم ثبت نشده.')} />
              : <ScrollTable head={
                  <tr><th className="px-3 py-2 text-start font-medium">{t('Insurer', 'بیمه‌گر')}</th>
                      <th className="px-3 py-2 text-center font-medium">{t('Covered', 'پوشش')}</th>
                      <th className="px-3 py-2 text-end font-medium">{t('Share %', 'سهم٪')}</th>
                      <th className="px-3 py-2 text-end font-medium">{t('Reference', 'مرجع')}</th>
                      <th className="px-3 py-2 text-end font-medium">{t('Ceiling', 'سقف')}</th>
                      <th className="px-3 py-2 text-start font-medium">{t('Match', 'تطبیق')}</th></tr>}>
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
            <h5 className="text-xs font-semibold text-slate-300 uppercase tracking-wide">{t('Same-molecule group', 'گروه هم‌مولکول')}</h5>
            {detail.siblings.length === 0 ? <Empty text={t('No other same-molecule item found.', 'هم‌مولکول دیگری یافت نشد.')} />
              : <ScrollTable head={
                  <tr><th className="px-3 py-2 text-start font-medium">{t('Name', 'نام')}</th>
                      <th className="px-3 py-2 text-start font-medium">{t('Strength', 'قدرت')}</th>
                      <th className="px-3 py-2 text-end font-medium">{t('Announced price', 'قیمت اعلامی')}</th>
                      <th className="px-3 py-2 text-end font-medium">{t('Insurer reference', 'مرجع بیمه')}</th></tr>}>
                  {detail.siblings.map(s => {
                    const cross = isCross(s)
                    return (
                      <tr key={s.irc} className={cross ? 'bg-amber-500/15' : 'hover:bg-slate-700/20'}>
                        <td className="px-3 py-2 text-right text-slate-200">
                          {s.name_fa}{cross && <span className="ms-1 text-[10px] text-amber-300">◄ {t('shared reference', 'مرجع مشترک')}</span>}</td>
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
            <h5 className="text-xs font-semibold text-slate-300 uppercase tracking-wide">{t('Analysis', 'تحلیل')}</h5>
            {detail.analysis.length === 0
              ? <Empty text={t('No analytical note found ✓', 'نکتهٔ تحلیلی‌ای یافت نشد ✓')} />
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
// Insurer names stay Persian in both languages — see the note on INSURERS.
const INSURER_FA: Record<string, string> = {
  tamin: 'تأمین اجتماعی', salamat: 'بیمه سلامت', armed: 'نیروهای مسلح',
}
const KIND_LABEL: Record<string, Pair> = {
  herbal: ['Herbal', 'گیاهی'], device: ['Device', 'تجهیزات'], bulk: ['Raw material', 'مادهٔ اولیه'],
  supply: ['Supply/container', 'لوازم/ظرف'], supplement: ['Supplement', 'مکمل'], other: ['Other', 'سایر'],
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
const REASON_LABEL: Record<string, Pair> = {
  unmatched: ['Unmatched', 'نامنطبق'], low_confidence: ['Low confidence', 'اطمینان پایین'],
  missing_details: ['Incomplete details', 'جزئیات ناقص'],
}

function EnrichmentPanel({ onMsg, onError }: {
  onMsg: (m: { kind: 'ok' | 'err'; text: string }) => void
  onError: (e: unknown, fallback: string) => void
}) {
  const { t, tp, n } = useLang()
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
    onMsg({ kind: 'ok', text: refresh ? t('Re-research of the previous items started…', 'پژوهش دوبارهٔ موارد قبلی آغاز شد…')
                                      : t('Research started…', 'پژوهش آغاز شد…') })
    try {
      await pricingApi.enrichRun({ limit, min_confidence: 0.7, workers, provider, refresh })
      qc.invalidateQueries({ queryKey: ['enrich-run-status'] })
    } catch (e) { onError(e, t('Could not start the research.', 'شروع پژوهش ناموفق بود.')) }
  }
  const stopRun = async () => {
    try {
      await pricingApi.enrichStop()
      onMsg({ kind: 'ok', text: t('Stop requested — the proposals already saved are kept.',
                                  'درخواست توقف ثبت شد — پیشنهادهای ذخیره‌شده حفظ می‌شوند.') })
      qc.invalidateQueries({ queryKey: ['enrich-run-status'] })
    } catch (e) { onError(e, t('Stopping failed.', 'توقف ناموفق بود.')) }
  }
  const testProvider = async () => {
    setBusy(true)
    try {
      const { data } = await pricingApi.enrichProviderTest(provider)
      const search = data.search_backend
        ? ` · ${t('search', 'جستجو')}: ${data.search_backend}${data.search_error ? ` ✗ (${data.search_error})`
            : ` ✓ (${t(`${data.search_results} results`, `${data.search_results} نتیجه`)})`}`
        : ''
      onMsg({
        kind: data.ok === false ? 'err' : 'ok',
        text: t(
          `${data.provider} connection ${data.ok === false ? 'is incomplete' : 'is up'} (${data.model || 'default model'}): ${data.reply || 'OK'}${search}`,
          `اتصال ${data.provider}${data.ok === false ? ' ناقص است' : ' برقرار است'} (${data.model || 'مدل پیش‌فرض'}): ${data.reply || 'OK'}${search}`),
      })
    } catch (e) { onError(e, t(`Could not reach ${provider}.`, `اتصال ${provider} برقرار نشد.`)) }
    finally { setBusy(false) }
  }
  const decide = async (ids: string[], approve: boolean) => {
    if (!ids.length) return
    setBusy(true)
    try {
      const { data } = await pricingApi.enrichDecide(ids, approve)
      onMsg({ kind: 'ok', text: approve ? t(`${n(data.updated)} approved.`, `${n(data.updated)} مورد تأیید شد.`)
                                        : t(`${n(data.updated)} rejected.`, `${n(data.updated)} مورد رد شد.`) })
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
        .map(([k, v]) => `${k}: ${n(v as number)}`).join(t(', ', '، '))
      onMsg({ kind: 'ok', text:
        t(`Match intelligence trained (${mode === 'bootstrap' ? 'all formularies' : 'your decisions'}) — `
          + `pairs: ${n(data.pairs?.pos)}+ / ${n(data.pairs?.neg)}− · `
          + `FS ${data.fs_armed ? 'armed ✓' : 'not armed (too few positive labels)'} · price bands: ${bands || '—'}`,
          `هوش تطبیق آموزش دید (${mode === 'bootstrap' ? 'کل دارونامه‌ها' : 'تصمیم‌های شما'}) — `
          + `جفت‌ها: ${n(data.pairs?.pos)}+ / ${n(data.pairs?.neg)}− · `
          + `FS ${data.fs_armed ? 'فعال ✓' : 'غیرفعال (برچسب مثبت کافی نیست)'} · باند قیمت: ${bands || '—'}`) })
    } catch (e) { onError(e, t('Training failed.', 'آموزش ناموفق بود.')) }
    finally { setBusy(false) }
  }
  const exportRef = async () => {
    try {
      const { data } = await pricingApi.enrichExport()
      onMsg({ kind: 'ok', text: t(`${n(data.exported)} approved rows written to ${data.path}.`,
                                  `${n(data.exported)} ردیف تأییدشده به ${data.path} نوشته شد.`) })
    } catch (e) { onError(e, t('Export failed.', 'برون‌سپاری ناموفق بود.')) }
  }
  const importRef = async () => {
    try {
      const { data } = await pricingApi.enrichImport()
      onMsg({ kind: 'ok', text: t(`${n(data.imported)} rows loaded from the reference.`,
                                  `${n(data.imported)} ردیف از مرجع بارگذاری شد.`) })
      qc.invalidateQueries({ queryKey: ['enrich-suggestions'] })
    } catch (e) { onError(e, t('Loading the reference failed.', 'بارگذاری مرجع ناموفق بود.')) }
  }
  const toggle = (id: string) => setSel(s => {
    const n = new Set(s); n.has(id) ? n.delete(id) : n.add(id); return n
  })

  return (
    <div className="bg-slate-800/50 border border-fuchsia-500/30 rounded-lg p-4 space-y-4" dir="rtl">
      <div className="flex items-center gap-2 flex-wrap">
        <h3 className="font-bold text-fuchsia-200">✨ {t('Drug intelligence — smart enrichment', 'هوش‌یار دارو — غنی‌سازی هوشمند')}</h3>
        <span className="text-[12px] text-slate-400">
          {t('Web research for unmatched and incomplete items; results are proposals and are applied only after you approve them.',
             'پژوهش وب برای اقلام نامنطبق و ناقص؛ نتایج «پیشنهادی» هستند و فقط پس از تأیید شما اعمال می‌شوند.')}
        </span>
      </div>

      {/* worklist summary */}
      <div className="flex flex-wrap items-center gap-2 text-[12px]">
        <span className="text-slate-300">{t('Worklist', 'فهرست کار')}: <b className="text-slate-100">{n(work?.total)}</b> {t('items', 'قلم')}</span>
        {Object.entries(work?.counts || {}).map(([reason, count]) => (
          <span key={reason} className="px-2 py-0.5 rounded-full bg-slate-700 border border-slate-600">
            {REASON_LABEL[reason] ? tp(REASON_LABEL[reason]) : reason}: {n(count)}
          </span>
        ))}
      </div>

      {/* run controls */}
      <div className="flex flex-wrap items-center gap-2">
        <label className="text-[12px] text-slate-400">{t('Engine', 'موتور جستجو')}</label>
        <select value={provider} onChange={e => setProvider(e.target.value)}
          className="bg-slate-900 border border-slate-600 rounded px-2 py-1 text-sm">
          <option value="mistral">{t('Mistral (slow — rate limited)', 'Mistral (کند — محدودیت نرخ)')}</option>
          <option value="gemini">{t('Gemini (free, extraction)', 'Gemini (رایگان، استخراج)')}</option>
        </select>
        {provider === 'gemini' && run?.search_backend && (
          <span className={`text-[11px] px-2 py-0.5 rounded-full border ${
            run.search_backend !== 'duckduckgo'
              ? 'bg-orange-500/10 border-orange-500/40 text-orange-300'
              : 'bg-slate-700 border-slate-600 text-slate-300'}`}>
            {t('Web search', 'جستجوی وب')}: {run.search_backend === 'youcom' ? 'You.com ✓'
              : run.search_backend === 'brave' ? 'Brave ✓' : t('DuckDuckGo (no key)', 'DuckDuckGo (بدون کلید)')}
          </span>
        )}
        <label className="text-[12px] text-slate-400">{t('Items this run', 'تعداد در این اجرا')}</label>
        <input type="number" min={1} max={500} value={limit}
          onChange={e => setLimit(Math.max(1, Math.min(500, Number(e.target.value) || 1)))}
          className="w-20 bg-slate-900 border border-slate-600 rounded px-2 py-1 text-sm tabular-nums" />
        <label className="text-[12px] text-slate-400">{t('Concurrency', 'هم‌زمانی')}</label>
        <input type="number" min={1} max={15} value={workers}
          onChange={e => setWorkers(Math.max(1, Math.min(15, Number(e.target.value) || 1)))}
          className="w-16 bg-slate-900 border border-slate-600 rounded px-2 py-1 text-sm tabular-nums" />
        <button onClick={() => startRun(false)} disabled={run?.running}
          className="px-3 py-1.5 text-sm rounded-md bg-fuchsia-600 hover:bg-fuchsia-500 disabled:opacity-40">
          {run?.running ? t('Researching…', 'در حال پژوهش…') : t('Start research', 'شروع پژوهش')}
        </button>
        <button onClick={() => startRun(true)} disabled={run?.running}
          title={t('Re-research every “proposed” item with the latest rules and match-intelligence changes',
                   'پژوهش دوبارهٔ همهٔ موارد «پیشنهادی» با آخرین قواعد و تغییرات هوش تطبیق')}
          className="px-3 py-1.5 text-sm rounded-md bg-fuchsia-800 hover:bg-fuchsia-700 disabled:opacity-40">
          🔄 {t('Research again', 'پژوهش دوباره')}
        </button>
        {run?.running && (
          <button onClick={stopRun} disabled={run.phase === 'cancelling'}
            className="px-3 py-1.5 text-sm rounded-md bg-red-700 hover:bg-red-600 disabled:opacity-40">
            {run.phase === 'cancelling' ? t('Stopping…', 'در حال توقف…') : `⏹ ${t('Stop', 'توقف')}`}
          </button>
        )}
        {!run?.running && (
          <button onClick={testProvider} disabled={busy}
            className="px-3 py-1.5 text-sm rounded-md bg-slate-700 hover:bg-slate-600 disabled:opacity-40">
            {t('Test connection', 'آزمون اتصال')}
          </button>
        )}
        <div className="mr-auto flex gap-2">
          <button onClick={() => retrainIntel('decisions')} disabled={busy}
            title={t('Only your approved/rejected pairs — definitive labels', 'فقط جفت‌های تأیید/ردشدهٔ شما — برچسب‌های قطعی')}
            className="px-2.5 py-1 text-[12px] rounded bg-cyan-800 hover:bg-cyan-700 disabled:opacity-40">
            🧠 {t('Train on decisions', 'آموزش از تصمیم‌ها')}</button>
          <button onClick={() => retrainIntel('bootstrap')} disabled={busy}
            title={t('Weak self-training over every formulary row — the linker’s most confident matches count as positives',
                   'خودآموزی ضعیف روی همهٔ ردیف‌های دارونامه‌ها — تطبیق‌های بسیار مطمئن لینکر به‌عنوان مثبت')}
            className="px-2.5 py-1 text-[12px] rounded bg-cyan-900 hover:bg-cyan-800 disabled:opacity-40">
            🧠 {t('Train on all formularies', 'آموزش بر کل دارونامه‌ها')}</button>
          <button onClick={exportRef} className="px-2.5 py-1 text-[12px] rounded bg-slate-700 hover:bg-slate-600">
            {t('Export reference', 'برون‌سپاری مرجع')} ⬇</button>
          <button onClick={importRef} className="px-2.5 py-1 text-[12px] rounded bg-slate-700 hover:bg-slate-600">
            {t('Import reference', 'بارگذاری مرجع')} ⬆</button>
        </div>
      </div>

      {/* live progress */}
      {run?.running && (
        <div className="bg-fuchsia-500/10 border border-fuchsia-500/40 rounded-lg p-3 text-[12px] font-mono space-y-1">
          <div className="flex flex-wrap gap-x-5">
            <span className="text-fuchsia-300">{t('phase', 'مرحله')}: {run.phase}</span>
            {run.provider && <span>{t('engine', 'موتور')}: {run.provider}</span>}
            <span>{t('concurrency', 'هم‌زمانی')}: {n(run.workers)}</span>
            {run.pace_sec > 0 && <span>{t('call spacing', 'فاصله فراخوانی')}: {run.pace_sec}s</span>}
            <span>{t('progress', 'پیشرفت')}: {n(run.done)}/{n(run.total)}</span>
            <span className="text-emerald-300">{t('saved', 'ثبت‌شده')}: {n(run.saved)}</span>
            <span className="text-slate-400">{t('skipped', 'ردشده')}: {n(run.skipped)}</span>
            <span className="text-red-400">{t('failed', 'ناموفق')}: {n(run.failed)}</span>
            <span>{run.elapsed_sec}s</span>
          </div>
          {/* current is self-describing now («۳ فعال: …» / «محدودیت نرخ؛ …») */}
          {run.current && <div className="text-slate-300 truncate">{run.current}</div>}
        </div>
      )}
      {run && !run.running && run.error &&
        <p className="text-[12px] text-red-400">{t('Last run error', 'خطای آخرین اجرا')}: {run.error}</p>}

      {/* per-item outcomes — without these a failed run shows only a count */}
      {(run?.recent || []).length > 0 && (
        <details className="text-[12px]" open={(run?.failed || 0) > 0}>
          <summary className="cursor-pointer text-slate-400 hover:text-slate-200">
            {t('Recent events', 'رویدادهای اخیر')} ({n(run?.recent.length)})
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
          <p className="font-semibold text-sm">{t('Review queue', 'صف بازبینی')} ({n(rows.length)})</p>
          {rows.length > 0 && <>
            <button onClick={() => setSel(new Set(rows.map(r => r.id)))}
              className="px-2 py-0.5 text-[12px] rounded bg-slate-700 hover:bg-slate-600">{t('Select all', 'انتخاب همه')}</button>
            <button onClick={() => decide([...sel], true)} disabled={busy || sel.size === 0}
              className="px-2 py-0.5 text-[12px] rounded bg-emerald-700 hover:bg-emerald-600 disabled:opacity-40">
              {t('Approve selected', 'تأیید انتخاب‌شده‌ها')} ({n(sel.size)})</button>
            <button onClick={() => decide([...sel], false)} disabled={busy || sel.size === 0}
              className="px-2 py-0.5 text-[12px] rounded bg-red-800 hover:bg-red-700 disabled:opacity-40">
              {t('Reject selected', 'رد انتخاب‌شده‌ها')}</button>
          </>}
        </div>
        {rows.length === 0
          ? <Empty text={t('No proposal is awaiting review.', 'هیچ پیشنهاد در انتظار بازبینی نیست.')} />
          : <div className="space-y-1.5">
              {rows.map(s => (
                <div key={s.id} className="flex flex-wrap items-start gap-x-3 gap-y-1 text-[12px] border border-slate-700/60 rounded-md p-2">
                  <input type="checkbox" checked={sel.has(s.id)} onChange={() => toggle(s.id)} className="mt-1" />
                  <div className="flex-1 min-w-[16rem] space-y-0.5">
                    <div className="font-semibold text-slate-100">
                      {s.raw_name}
                      {s.item_kind && s.item_kind !== 'drug' && (
                        <span className="mr-2 text-[10px] px-1.5 py-0.5 rounded bg-amber-500/15 border border-amber-500/40 text-amber-300 align-middle">
                          {KIND_LABEL[s.item_kind] ? tp(KIND_LABEL[s.item_kind]) : s.item_kind}
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
                            {t('formulary', 'دارونامهٔ')} {INSURER_FA[s.context?.insurer || ''] || s.context?.insurer || '—'}
                          </p>
                          <p className="text-[11px] text-slate-200">{s.raw_name}</p>
                          <div className="flex flex-wrap gap-x-3 text-[11px] text-slate-400 tabular-nums">
                            {s.context?.reference_price != null &&
                              <span>{t('reference price', 'قیمت مرجع')}: {n(Number(s.context.reference_price))}</span>}
                            {s.context?.share_pct != null &&
                              <span>{t('insurer share', 'سهم بیمه')}: {n(Number(s.context.share_pct))}{t('%', '٪')}</span>}
                            {s.context?.covered != null &&
                              <span>{s.context.covered ? t('covered', 'تحت پوشش') : t('not covered', 'بدون پوشش')}</span>}
                            {s.context?.reference_price == null && s.context?.share_pct == null &&
                              <span className="text-slate-600">{t('no formulary data recorded', 'داده‌ای از دارونامه ثبت نشده')}</span>}
                          </div>
                        </div>
                        <div className="rounded border border-sky-500/30 bg-sky-500/5 p-2 space-y-1">
                          <p className="text-[10px] text-sky-300/80">
                            {t('NFI catalog candidates', 'نامزدهای کاتالوگ NFI')} ({n((s.nfi_candidates || []).length)})
                          </p>
                          {(s.nfi_candidates || []).length === 0
                            ? <p className="text-[11px] text-slate-600">{t('No similar candidate found', 'نامزد مشابهی یافت نشد')}</p>
                            : (s.nfi_candidates || []).map(c => (
                              <div key={c.irc} className="text-[11px] border-b border-slate-700/40 pb-0.5">
                                <div className="text-slate-200">{c.name_fa}</div>
                                <div className="flex flex-wrap gap-x-2 text-slate-500 tabular-nums">
                                  <span className="font-mono">{c.irc}</span>
                                  {c.strength && <span>{c.strength}</span>}
                                  {c.dosage_form && <span>{c.dosage_form}</span>}
                                  {c.announced_price != null && <span>{fa(c.announced_price)} ﷼</span>}
                                  <span className="text-sky-400">{t('similarity', 'شباهت')} {c.similarity}</span>
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
                          title={t('Match-intelligence score — negative means the research is suspect',
                                   'امتیاز هوش تطبیق — منفی یعنی پژوهش مشکوک است')}>
                          FS {s.fs_score}
                        </span>
                      )}
                      {s.confidence != null &&
                        <span>{t('confidence', 'اطمینان')}: {n(Math.round(s.confidence * 100))}{t('%', '٪')}</span>}
                      {(s.sources || []).slice(0, 3).map((u, i) => (
                        <a key={i} href={u} target="_blank" rel="noreferrer"
                          className="text-indigo-300 hover:underline truncate max-w-[14rem]">{t('source', 'منبع')} {n(i + 1)}↗</a>))}
                    </div>
                  </div>
                  <div className="flex gap-1.5">
                    <button onClick={() => decide([s.id], true)} disabled={busy}
                      className="px-2 py-0.5 rounded bg-emerald-700 hover:bg-emerald-600 disabled:opacity-40">{t('Approve', 'تأیید')}</button>
                    <button onClick={() => decide([s.id], false)} disabled={busy}
                      className="px-2 py-0.5 rounded bg-red-800 hover:bg-red-700 disabled:opacity-40">{t('Reject', 'رد')}</button>
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
  // set when the price came from an insurer feed rather than an NFI-verified
  // consumer price — kind 'gap_fill' (had no price) or 'refresh' (was stale)
  price_provenance?: { announced: string; source: string; kind: string
                       previous: number | null; at: string; note: string } | null
}

function PriceProvenanceBadge({ p }: { p: CatalogItem['price_provenance'] }) {
  const { t, n } = useLang()
  if (!p || p.announced !== 'insurer-derived') return null
  const fill = p.kind === 'gap_fill'
  return (
    <span title={`${p.note} — ${t('source', 'منبع')}: ${p.source}${
        p.previous ? ` · ${t('previous', 'پیشین')}: ${n(p.previous)}` : ''}`}
      className={`ms-2 px-1.5 py-0.5 rounded text-[10px] border align-middle ${
        fill ? 'border-amber-500/50 bg-amber-500/10 text-amber-300'
             : 'border-cyan-500/40 bg-cyan-500/10 text-cyan-300'}`}>
      {fill ? t('⚠ price from the insurer (unconfirmed)', '⚠ قیمت از بیمه (تأییدنشده)')
            : t('↻ refreshed from the insurer', '↻ به‌روزشده از بیمه')}
    </span>)
}
// label, key, input kind — the owner-correctable surface of an NFI row
const EDIT_FIELDS: [Pair, keyof CatalogItem, 'text' | 'num' | 'bool'][] = [
  [['Persian name', 'نام فارسی'], 'name_fa', 'text'], [['Latin name', 'نام لاتین'], 'name_en', 'text'],
  [['Generic (active ingredient)', 'ژنریک (مادهٔ مؤثره)'], 'generic_name', 'text'],
  [['Dosage form', 'شکل دارویی'], 'dosage_form', 'text'],
  [['Strength', 'قدرت'], 'strength', 'text'], [['Brand', 'برند'], 'brand_name', 'text'],
  [['Manufacturer', 'تولیدکننده'], 'manufacturer', 'text'], [['Country', 'کشور'], 'country', 'text'],
  [['ATC', 'ATC'], 'atc', 'text'], [['Units per pack', 'تعداد در بسته'], 'package_count', 'num'],
  [['Barcode (GTIN)', 'بارکد (GTIN)'], 'gtin', 'text'], [['e-Rx code', 'کد نسخه الکترونیک'], 'erx_code', 'text'],
  [['Licence holder', 'صاحب پروانه'], 'license_owner', 'text'], [['Brand owner', 'صاحب برند'], 'brand_owner', 'text'],
  [['Licence valid until', 'اعتبار پروانه'], 'license_valid_until', 'text'], [['Category', 'دسته'], 'category', 'text'],
  [['Announced price', 'قیمت اعلامی'], 'announced_price', 'num'],
  [['Invoice price', 'قیمت فاکتور'], 'last_invoice_price', 'num'],
  [['Is generic', 'ژنریک است'], 'is_generic', 'bool'], [['Is OTC', 'OTC است'], 'is_otc', 'bool'],
]
const MISSING_FILTERS: [string, Pair][] = [
  ['', ['All', 'همه']], ['price', ['No price', 'بدون قیمت']], ['generic', ['No generic', 'بدون ژنریک']],
  ['country', ['No country', 'بدون کشور']], ['atc', ['No ATC', 'بدون ATC']], ['form', ['No form', 'بدون شکل']],
  ['strength', ['No strength', 'بدون قدرت']],
]

function CatalogEditorPanel({ onMsg, onError }: {
  onMsg: (m: { kind: 'ok' | 'err'; text: string }) => void
  onError: (e: unknown, fallback: string) => void
}) {
  const { t, tp, n } = useLang()
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
    if (!Object.keys(changed).length) { onMsg({ kind: 'ok', text: t('Nothing changed to save.', 'تغییری برای ذخیره نیست.') }); return }
    setBusy(true)
    try {
      const { data: res } = await pricingApi.catalogEdit(open.irc, changed, 'اصلاح دستی از کاتالوگ')
      onMsg({ kind: 'ok', text: t(`${n(Object.keys(res.changed || {}).length)} fields corrected on ${open.irc}.`,
                                  `${n(Object.keys(res.changed || {}).length)} فیلد در ${open.irc} اصلاح شد.`) })
      qc.invalidateQueries({ queryKey: ['catalog-items'] })
      setOpenIrc(null)
    } catch (e) { onError(e, t('Saving the correction failed.', 'ذخیرهٔ اصلاح ناموفق بود.')) } finally { setBusy(false) }
  }

  return (
    <div className="bg-slate-800/50 border border-violet-500/30 rounded-lg p-4 space-y-3" dir="rtl">
      <div className="flex items-baseline gap-2 flex-wrap">
        <h3 className="font-bold text-violet-200">🗂 {t('NFI catalog', 'کاتالوگ NFI')}</h3>
        <span className="text-[12px] text-slate-400">
          {t('Browse every column and correct any row — inconsistencies usually start as an error in this very list.',
             'مرور همهٔ ستون‌ها و اصلاح هر قلم — ناسازگاری‌ها اغلب از خطای همین فهرست‌اند.')}
        </span>
      </div>

      <div className="flex flex-wrap items-center gap-2 text-[12px]">
        <input type="search" value={q} onChange={e => { setQ(e.target.value); setOffset(0) }}
          placeholder={t('Search: IRC, name, generic, brand…', 'جست‌وجو: IRC، نام، ژنریک، برند…')}
          className="flex-1 min-w-[14rem] bg-slate-900 border border-slate-600 rounded px-3 py-1" />
        <select value={missing} onChange={e => { setMissing(e.target.value); setOffset(0) }}
          className="bg-slate-900 border border-slate-600 rounded px-2 py-1">
          {MISSING_FILTERS.map(([v, l]) => <option key={v} value={v}>{tp(l)}</option>)}
        </select>
        <span className="text-slate-400">{t(`${n(data?.total)} items`, `${n(data?.total)} قلم`)}</span>
      </div>

      <ScrollTable head={
        <tr><th className="px-3 py-2 text-start font-medium">{t('Name', 'نام')}</th>
            <th className="px-3 py-2 text-start font-medium">{t('Generic', 'ژنریک')}</th>
            <th className="px-3 py-2 text-start font-medium">{t('Form / strength', 'شکل / قدرت')}</th>
            <th className="px-3 py-2 text-end font-medium">{t('Price', 'قیمت')}</th>
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
            <td className="px-3 py-2 text-left tabular-nums text-slate-300 whitespace-nowrap">
              {fa(it.announced_price)}<PriceProvenanceBadge p={it.price_provenance} />
            </td>
            <td className="px-3 py-2 text-left">
              <button onClick={() => startEdit(it)}
                className="px-2 py-0.5 text-[11px] rounded bg-violet-700 hover:bg-violet-600">{t('Edit', 'ویرایش')}</button>
            </td>
          </tr>))}
      </ScrollTable>

      <div className="flex items-center gap-2 text-[12px]">
        <button onClick={() => setOffset(Math.max(0, offset - limit))} disabled={offset === 0}
          className="px-2 py-0.5 rounded bg-slate-700 hover:bg-slate-600 disabled:opacity-40">{t('Previous', 'قبلی')}</button>
        <span className="text-slate-500">{fa(offset + 1)}–{fa(Math.min(offset + limit, data?.total || 0))}</span>
        <button onClick={() => setOffset(offset + limit)}
          disabled={offset + limit >= (data?.total || 0)}
          className="px-2 py-0.5 rounded bg-slate-700 hover:bg-slate-600 disabled:opacity-40">{t('Next', 'بعدی')}</button>
      </div>

      {open && (
        <div className="border border-violet-500/40 rounded-lg p-3 space-y-3">
          <div className="flex items-center gap-2 flex-wrap">
            <p className="font-semibold text-sm text-violet-200">{t('Edit', 'ویرایش')} {open.name_fa}</p>
            <span className="text-[11px] font-mono text-slate-500">{open.irc}</span>
            <button onClick={() => setOpenIrc(null)}
              className="ms-auto text-slate-400 hover:text-slate-200 text-[12px]">{t('Close', 'بستن')} ✕</button>
          </div>
          <div className="grid sm:grid-cols-2 lg:grid-cols-3 gap-2">
            {EDIT_FIELDS.map(([label, key, kind]) => (
              <label key={key as string} className="space-y-0.5 text-[11px]">
                <span className="text-slate-400">{tp(label)}</span>
                {kind === 'bool' ? (
                  <select value={String(draft[key] ?? false)}
                    onChange={e => setDraft(d => ({ ...d, [key]: e.target.value === 'true' }))}
                    className="w-full bg-slate-900 border border-slate-600 rounded px-2 py-1">
                    <option value="true">{t('Yes', 'بله')}</option><option value="false">{t('No', 'خیر')}</option>
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
              {t('Save correction', 'ذخیرهٔ اصلاح')}
            </button>
            <span className="text-[11px] text-slate-500">
              {t('A price change is written to the price history as a dated point; the compounding key is recalculated automatically.',
                 'تغییر قیمت به‌صورت نقطهٔ تاریخ‌دار در تاریخچهٔ قیمت ثبت می‌شود؛ کلید ترکیب خودکار بازمحاسبه می‌گردد.')}
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
const DECISION_REASON: Record<string, Pair> = {
  wrong_product: ['Different drug', 'داروی دیگر'], wrong_strength: ['Wrong strength', 'قدرت اشتباه'],
  wrong_form: ['Wrong dosage form', 'شکل اشتباه'], wrong_brand: ['Wrong brand', 'برند اشتباه'],
  wrong_pack: ['Wrong pack', 'بستهٔ اشتباه'], price_implausible: ['Implausible price', 'قیمت نامعقول'],
  other: ['Other', 'سایر'],
}

function DecisionsPanel({ onMsg, onError }: {
  onMsg: (m: { kind: 'ok' | 'err'; text: string }) => void
  onError: (e: unknown, fallback: string) => void
}) {
  const { t, tp, n } = useLang()
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
      t(`Undo the “${o.field}” correction on ${o.irc}? The value reverts to whatever the source publishes.`,
        `لغو اصلاح «${o.field}» برای ${o.irc}؟ مقدار به آنچه منبع منتشر می‌کند برمی‌گردد.`))) return
    setBusy(true)
    try {
      await pricingApi.overrideDelete(o.id)
      onMsg({ kind: 'ok', text: t(`The ${o.field} correction was undone — from the next crawl, the source value returns.`,
                                  `اصلاح ${o.field} لغو شد — از خزش بعدی، مقدار منبع برمی‌گردد.`) })
      qc.invalidateQueries({ queryKey: ['overrides'] })
    } catch (e) { onError(e, t('Undoing the correction failed.', 'لغو اصلاح ناموفق بود.')) } finally { setBusy(false) }
  }

  return (
    <div className="space-y-4" dir="rtl">
      <RevisionPanel onMsg={onMsg} onError={onError} />
      <SuccessionPanel onMsg={onMsg} onError={onError} />
      {/* crosswalk: insurer row ↔ our product, decided once */}
      <div className="bg-slate-800/50 border border-teal-500/30 rounded-lg p-4 space-y-3">
        <div className="flex items-baseline gap-2 flex-wrap">
          <h3 className="font-bold text-teal-200">⚖ {t('Settled mappings (crosswalk)', 'نگاشت‌های قطعی (Crosswalk)')}</h3>
          <span className="text-[12px] text-slate-400">
            {t('Each ruling of yours is recorded once and reapplied in every later harvest without a second review.',
               'هر رأی شما یک‌بار ثبت می‌شود و در همهٔ برداشت‌های بعدی بدون بازبینی دوباره اعمال می‌گردد.')}
          </span>
          <span className="mr-auto flex gap-2">
            <button onClick={async () => {
              setBusy(true)
              try {
                const { data } = await pricingApi.canonicalExport()
                const c = data.counts || {}
                onMsg({ kind: 'ok', text:
                  t(`Canonical bundle written — catalog ${n(c.catalog)}, mappings ${n(c.crosswalk)}, corrections ${n(c.overrides)}, current prices ${n(c.prices_current)} (data/canonical/)`,
                    `بستهٔ متعارف نوشته شد — کاتالوگ ${n(c.catalog)}، نگاشت ${n(c.crosswalk)}، اصلاح ${n(c.overrides)}، قیمت جاری ${n(c.prices_current)} (data/canonical/)`) })
              } catch (e) { onError(e, t('Exporting the bundle failed.', 'برون‌سپاری بسته ناموفق بود.')) } finally { setBusy(false) }
            }} disabled={busy}
              title={t('Catalog + resolved formularies + mappings + corrections + current prices, with a checksum manifest',
                       'کاتالوگ + دارونامه‌های حل‌شده + نگاشت‌ها + اصلاح‌ها + قیمت‌های جاری، با مانیفست checksum')}
              className="px-2.5 py-1 text-[12px] rounded bg-teal-800 hover:bg-teal-700 disabled:opacity-40">
              📦 {t('Export canonical bundle', 'برون‌سپاری بستهٔ متعارف')}
            </button>
            <button onClick={async () => {
              setBusy(true)
              try {
                const { data } = await pricingApi.canonicalImport()
                onMsg({ kind: 'ok', text: t(`Decision layer reloaded — ${n(data.crosswalk)} mappings, ${n(data.overrides)} corrections.`,
                                            `لایهٔ تصمیم بازخوانی شد — ${n(data.crosswalk)} نگاشت، ${n(data.overrides)} اصلاح.`) })
                qc.invalidateQueries({ queryKey: ['crosswalk'] })
                qc.invalidateQueries({ queryKey: ['overrides'] })
              } catch (e) { onError(e, t('Reloading the bundle failed.', 'بازخوانی بسته ناموفق بود.')) } finally { setBusy(false) }
            }} disabled={busy}
              className="px-2.5 py-1 text-[12px] rounded bg-slate-700 hover:bg-slate-600 disabled:opacity-40">
              {t('Reload the decision layer', 'بازخوانی لایهٔ تصمیم')}
            </button>
          </span>
        </div>
        <div className="flex flex-wrap items-center gap-2 text-[12px]">
          <select value={insurer} onChange={e => setInsurer(e.target.value)}
            className="bg-slate-900 border border-slate-600 rounded px-2 py-1">
            <option value="">{t('All insurers', 'همهٔ بیمه‌گرها')}</option>
            <option value="tamin">تأمین اجتماعی</option>
            <option value="salamat">بیمه سلامت</option>
            <option value="armed">نیروهای مسلح</option>
          </select>
          <select value={status} onChange={e => setStatus(e.target.value)}
            className="bg-slate-900 border border-slate-600 rounded px-2 py-1">
            <option value="">{t('All statuses', 'همهٔ وضعیت‌ها')}</option>
            <option value="confirmed">{t('Confirmed', 'تأییدشده')}</option>
            <option value="rejected">{t('Rejected', 'ردشده')}</option>
          </select>
          <span className="text-slate-400">{t(`${n(cw?.total)} decisions`, `${n(cw?.total)} تصمیم`)}</span>
        </div>
        {(cw?.entries || []).length === 0
          ? <Empty text={t('No decision recorded yet — your rulings accumulate here from the next “Apply run”.',
                            'هنوز تصمیمی ثبت نشده — با «اعمال اجرا»ی بعدی، رأی‌های شما اینجا انباشته می‌شوند.')} />
          : <ScrollTable head={
              <tr><th className="px-3 py-2 text-start font-medium">{t('Formulary item', 'قلم دارونامه')}</th>
                  <th className="px-3 py-2 text-start font-medium">{t('Ruling', 'حکم')}</th>
                  <th className="px-3 py-2 text-start font-medium">{t('Product (IRC)', 'محصول (IRC)')}</th>
                  <th className="px-3 py-2 text-start font-medium">{t('Date', 'تاریخ')}</th></tr>}>
              {(cw?.entries || []).map(e => (
                <tr key={e.id} className="hover:bg-slate-700/25">
                  <td className="px-3 py-2 text-right">
                    <div className="text-slate-200">{e.raw_name || e.raw_key}</div>
                    <div className="text-[11px] text-slate-500">
                      {e.insurer}{e.source_code && <span className="font-mono"> · {t('code', 'کد')} {e.source_code}</span>}
                    </div>
                  </td>
                  <td className="px-3 py-2 text-right">
                    <span className={`text-[11px] px-1.5 py-0.5 rounded border ${
                      e.status === 'confirmed'
                        ? 'bg-emerald-500/15 border-emerald-500/40 text-emerald-300'
                        : 'bg-red-500/15 border-red-500/40 text-red-300'}`}>
                      {e.status === 'confirmed' ? t('Approved', 'تأیید') : t('Rejected', 'رد')}
                      {e.reason && ` — ${DECISION_REASON[e.reason] ? tp(DECISION_REASON[e.reason]) : e.reason}`}
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
          <h3 className="font-bold text-violet-200">🛡 {t('Durable catalog corrections', 'اصلاح‌های ماندگار کاتالوگ')}</h3>
          <span className="text-[12px] text-slate-400">
            {t('These values are reapplied after every NFI crawl; undo ⇒ back to the source value.',
               'این مقادیر پس از هر خزش NFI دوباره اعمال می‌شوند؛ لغو ⇒ بازگشت به مقدار منبع.')}
          </span>
          <span className="ms-auto text-[12px] text-slate-400">{t(`${n(ov?.total)} corrections`, `${n(ov?.total)} اصلاح`)}</span>
        </div>
        {(ov?.overrides || []).length === 0
          ? <Empty text={t('No durable correction recorded — catalog edits land here by default.',
                            'اصلاح ماندگاری ثبت نشده — ویرایش‌های کاتالوگ به‌صورت پیش‌فرض اینجا می‌آیند.')} />
          : <ScrollTable head={
              <tr><th className="px-3 py-2 text-start font-medium">{t('Product', 'محصول')}</th>
                  <th className="px-3 py-2 text-start font-medium">{t('Field', 'فیلد')}</th>
                  <th className="px-3 py-2 text-start font-medium">{t('Owner value', 'مقدار مالک')}</th>
                  <th className="px-3 py-2 text-start font-medium">{t('Reason', 'دلیل')}</th>
                  <th className="px-3 py-2 text-left font-medium"></th></tr>}>
              {(ov?.overrides || []).map(o => (
                <tr key={o.id} className="hover:bg-slate-700/25">
                  <td className="px-3 py-2 text-right font-mono text-[11px] text-slate-400">{o.irc}</td>
                  <td className="px-3 py-2 text-right text-slate-300">{o.field}</td>
                  <td className="px-3 py-2 text-right text-slate-200">
                    {o.value ?? <span className="text-slate-500">{t('(forced empty)', '(خالی اجباری)')}</span>}</td>
                  <td className="px-3 py-2 text-right text-[11px] text-slate-500">{o.reason || '—'}</td>
                  <td className="px-3 py-2 text-left">
                    <button onClick={() => revoke(o)} disabled={busy}
                      className="px-2 py-0.5 text-[11px] rounded bg-red-800 hover:bg-red-700 disabled:opacity-40">
                      {t('Undo', 'لغو')}</button>
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
  const { t, n } = useLang()
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
      onMsg({ kind: 'ok', text: t('Tamin harvest started — continuing from the last saved page.',
                                  'برداشت تأمین آغاز شد — ادامه از آخرین صفحهٔ ذخیره‌شده.') })
      qc.invalidateQueries({ queryKey: ['tamin-harvest'] })
    } catch (e) { onError(e, t('Could not start the harvest.', 'شروع برداشت ناموفق بود.')) } finally { setBusy(false) }
  }
  const stop = async () => {
    try {
      await pricingApi.taminHarvestStop()
      onMsg({ kind: 'ok', text: t('Stop requested — the rows already saved are kept.',
                                  'توقف درخواست شد — ردیف‌های ذخیره‌شده حفظ می‌شوند.') })
      qc.invalidateQueries({ queryKey: ['tamin-harvest'] })
    } catch (e) { onError(e, t('Stopping failed.', 'توقف ناموفق بود.')) }
  }

  const out = st?.outputs || {}
  return (
    <div className="bg-slate-800/50 border border-emerald-500/25 rounded-lg p-4 space-y-4" dir="rtl">
      <div className="flex items-baseline gap-2 flex-wrap">
        <h3 className="font-bold text-emerald-200">{t('Social Security formulary harvest', 'برداشت دارونامهٔ تأمین اجتماعی')}</h3>
        <span className="text-[12px] text-slate-400">
          {t('Page by page through the ASPxGridView system; an unfinished run picks up where it stopped.',
             'صفحه‌به‌صفحه از سامانهٔ ASPxGridView؛ با «ادامه» اجرای نیمه‌تمام از همان‌جا دنبال می‌شود.')}
        </span>
      </div>

      {/* settings — defaults match the command the owner runs by hand */}
      <div className="grid sm:grid-cols-3 gap-2 text-[12px]">
        <label className="space-y-1">
          <span className="text-slate-400">{t('Starting HTML file', 'فایل HTML شروع')}</span>
          <input value={inputHtml} onChange={e => setInputHtml(e.target.value)}
            placeholder={t('default: Downloads/معاونت درمان…html', 'پیش‌فرض: Downloads/معاونت درمان…html')}
            className="w-full bg-slate-900 border border-slate-600 rounded px-2 py-1 font-mono text-[11px]" />
        </label>
        <label className="space-y-1">
          <span className="text-slate-400">{t('Pause between requests (s)', 'مکث بین درخواست‌ها (ثانیه)')}</span>
          <input type="number" min={0} max={30} value={delay}
            onChange={e => setDelay(Number(e.target.value) || 0)}
            className="w-full bg-slate-900 border border-slate-600 rounded px-2 py-1 tabular-nums" />
        </label>
        <label className="space-y-1">
          <span className="text-slate-400">{t('Per-request timeout (s)', 'مهلت هر درخواست (ثانیه)')}</span>
          <input type="number" min={10} max={600} value={timeout_}
            onChange={e => setTimeout_(Number(e.target.value) || 120)}
            className="w-full bg-slate-900 border border-slate-600 rounded px-2 py-1 tabular-nums" />
        </label>
      </div>

      <div className="flex items-center gap-2 flex-wrap">
        <button onClick={start} disabled={busy || st?.running}
          className="px-3 py-1.5 text-sm rounded-md bg-emerald-600 hover:bg-emerald-500 disabled:opacity-40">
          {st?.running ? t('Harvesting…', 'در حال برداشت…') : t('Start harvest', 'شروع برداشت')}
        </button>
        {st?.running && (
          <button onClick={stop}
            className="px-3 py-1.5 text-sm rounded-md bg-red-700 hover:bg-red-600">⏹ {t('Stop', 'توقف')}</button>)}
        <span className="text-[11px] text-slate-500">
          {t('An Iran proxy must be up; network errors are retried up to 20 times.',
             'پروکسی ایران باید فعال باشد؛ خطاهای شبکه تا ۲۰ بار بازآزمایی می‌شوند.')}
        </span>
      </div>

      {/* live progress */}
      {(st?.running || st?.page) ? (
        <div className="space-y-1.5">
          <div className="flex flex-wrap gap-x-5 text-[12px] font-mono">
            <span className={st?.running ? 'text-emerald-300' : 'text-slate-400'}>
              {t('phase', 'مرحله')}: {st?.phase}
            </span>
            <span>{t('page', 'صفحه')}: {n(st?.page)}/{n(st?.page_count)}</span>
            <span>{t('rows this run', 'ردیف‌های این اجرا')}: {n(st?.rows)}</span>
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
          ? <span>CSV: {t(`${n(out.csv.rows)} rows`, `${n(out.csv.rows)} ردیف`)} · {n(Math.round(out.csv.bytes / 1024))}KB ·
              {' '}{new Date(out.csv.mtime * 1000).toLocaleString('fa-IR')}</span>
          : <span className="text-slate-600">{t('No CSV produced yet.', 'هنوز CSV تولید نشده.')}</span>}
        {out.json && <span>JSON: {fa(Math.round(out.json.bytes / 1024))}KB</span>}
      </div>

      {(st?.log || []).length > 0 && (
        <details className="text-[11px]">
          <summary className="cursor-pointer text-slate-400 hover:text-slate-200">
            {t('Run log', 'گزارش اجرا')} ({t(`${n(st?.log.length)} lines`, `${n(st?.log.length)} خط`)})
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
const PTYPE_LABEL: Record<string, Pair> = {
  announced: ['Announced', 'اعلامی'], invoice: ['Invoice', 'فاکتور'],
  insurer_reference: ['Insurer reference', 'مرجع بیمه'],
}

function PriceHistoryPanel({ onMsg, onError }: {
  onMsg: (m: { kind: 'ok' | 'err'; text: string }) => void
  onError: (e: unknown, fallback: string) => void
}) {
  const { t, tp, n, date } = useLang()
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
      onMsg({ kind: 'ok', text: t(`${n(data.recorded)} price points recorded from the catalog.`,
                                  `${n(data.recorded)} نقطهٔ قیمت از کاتالوگ ثبت شد.`) })
      qc.invalidateQueries({ queryKey: ['price-stale'] })
    } catch (e) { onError(e, t('Seeding the prices failed.', 'ثبت اولیهٔ قیمت‌ها ناموفق بود.')) } finally { setBusy(false) }
  }

  return (
    <div className="bg-slate-800/50 border border-cyan-500/30 rounded-lg p-4 space-y-3" dir="rtl">
      <div className="flex items-center gap-2 flex-wrap">
        <h3 className="font-bold text-cyan-200">📉 {t('Price history', 'تاریخچهٔ قیمت')}</h3>
        <span className="text-[12px] text-slate-400">
          {t('Every price change is a dated point; “staleness” is a query, not a guess.',
             'هر تغییر قیمت یک نقطهٔ تاریخ‌دار است؛ «کهنگی» یک پرس‌وجو است، نه حدس.')}
        </span>
        <button onClick={backfill} disabled={busy}
          title={t('Record the catalog’s current prices as the start of the series',
                   'ثبت قیمت‌های فعلی کاتالوگ به‌عنوان نقطهٔ شروع سری زمانی')}
          className="mr-auto px-2.5 py-1 text-[12px] rounded bg-slate-700 hover:bg-slate-600 disabled:opacity-40">
          {t('Seed from the catalog', 'ثبت اولیه از کاتالوگ')}
        </button>
      </div>

      <div className="flex flex-wrap items-center gap-2 text-[12px]">
        <label className="text-slate-400">{t('Older than', 'کهنه‌تر از')}</label>
        <select value={ageDays} onChange={e => setAgeDays(Number(e.target.value))}
          className="bg-slate-900 border border-slate-600 rounded px-2 py-1">
          <option value={90}>{t('90 days', '۹۰ روز')}</option>
          <option value={180}>{t('180 days', '۱۸۰ روز')}</option>
          <option value={365}>{t('1 year', '۱ سال')}</option>
        </select>
        <span className={`px-2 py-0.5 rounded-full border ${(stale?.count || 0) > 0
          ? 'bg-amber-500/10 border-amber-500/40 text-amber-300'
          : 'bg-emerald-500/10 border-emerald-500/30 text-emerald-300'}`}>
          {t(`${n(stale?.count)} stale prices`, `${n(stale?.count)} قیمت کهنه`)}
        </span>
      </div>

      {(stale?.samples || []).length === 0
        ? <Empty text={t('No stale price in this window ✓', 'قیمت کهنه‌ای در این بازه نیست ✓')} />
        : <div className="max-h-56 overflow-y-auto space-y-0.5">
            {(stale?.samples || []).map(s => (
              <div key={s.irc} className="flex flex-wrap items-center gap-3 text-[12px] font-mono
                                          border-b border-slate-700/50 pb-1">
                <button onClick={() => setOpenIrc(openIrc === s.irc ? null : s.irc)}
                  className="text-cyan-300 hover:text-cyan-100">{s.irc}</button>
                <span className="tabular-nums">{fa(s.value)} ﷼</span>
                <span className="text-slate-500">
                  {t('since', 'از')} {date(s.since, { short: true })}
                </span>
                {s.source && <span className="text-slate-600">{s.source}</span>}
              </div>))}
          </div>}

      {openIrc && (
        <div className="border border-slate-700 rounded p-2 space-y-1">
          <p className="text-[12px] font-semibold text-slate-300">{t('Price trend', 'روند قیمت')} — {openIrc}</p>
          {(series?.history || []).length === 0
            ? <Empty text={t('No point recorded.', 'نقطه‌ای ثبت نشده.')} />
            : <div className="max-h-48 overflow-y-auto space-y-0.5 font-mono text-[11px]">
                {(series?.history || []).map((p, i) => (
                  <div key={i} className="flex flex-wrap items-center gap-3 border-b border-slate-700/40 pb-0.5">
                    <span className={p.current ? 'text-emerald-300' : 'text-slate-500'}>
                      {p.current ? `● ${t('current', 'جاری')}` : `○ ${t('previous', 'قبلی')}`}
                    </span>
                    <span className="text-slate-300">{PTYPE_LABEL[p.price_type] ? tp(PTYPE_LABEL[p.price_type]) : p.price_type}</span>
                    {p.insurer && <span className="text-indigo-300">{p.insurer}</span>}
                    <span className="tabular-nums">{fa(p.value)} ﷼</span>
                    <span className="text-slate-500">
                      {p.valid_from ? new Date(p.valid_from).toLocaleDateString('fa-IR') : '—'}
                      {' → '}
                      {p.valid_to ? date(p.valid_to, { short: true }) : t('now', 'اکنون')}
                    </span>
                    {p.source && <span className="text-slate-600">{p.source}</span>}
                  </div>))}
              </div>}
        </div>
      )}
    </div>
  )
}

// ── بازبینی تصمیم‌ها ────────────────────────────────────────────────────────
// The engine that made a decision months ago no longer exists: the ingredient
// floor moved, structural matching arrived, price began picking the form, the FS
// model was refitted. This panel asks today's engine the same questions and
// shows only where it now disagrees — and every ruling here is a training label,
// so revising is how the owner corrects the engine rather than just the table.
interface DecisionSide {
  irc: string | null; name_fa?: string | null; generic?: string | null
  strength?: string | null; dosage_form?: string | null; price?: number | null
  confidence?: number | null; method?: string | null; fs?: number | null; exists: boolean
}
interface DecisionItem {
  id: string; insurer: string; verdict: string; raw_name: string | null
  code: string | null; status: string; origin: string
  stored: DecisionSide; engine: DecisionSide
}
interface DecisionBoard {
  total: number; revised: number
  by_insurer: Record<string, number>; by_origin: Record<string, number>
  by_status: Record<string, number>
  last_pass?: Record<string, number | string> | null
}
const REVISION_VERDICT: Record<string, Pair> = {
  agree: ['Agrees', 'هم‌نظر'], moved: ['Moved', 'جابه‌جا شده'], lost: ['Inconclusive', 'بی‌نتیجه'],
  stale_target: ['Target removed', 'مقصد حذف شده'], revived: ['Rejection worth revisiting', 'رد قابل بازنگری'],
}
const VERDICT_TONE: Record<string, string> = {
  agree: 'bg-emerald-500/10 border-emerald-500/30 text-emerald-300',
  moved: 'bg-amber-500/10 border-amber-500/40 text-amber-300',
  lost: 'bg-slate-700 border-slate-600 text-slate-300',
  stale_target: 'bg-red-500/10 border-red-500/40 text-red-300',
  revived: 'bg-indigo-500/10 border-indigo-500/40 text-indigo-300',
}

function RevisionPanel({ onMsg, onError }: {
  onMsg: (m: { kind: 'ok' | 'err'; text: string }) => void
  onError: (e: unknown, fallback: string) => void
}) {
  const { t, tp, n } = useLang()
  const qc = useQueryClient()
  const [busy, setBusy] = useState(false)
  const [verdict, setVerdict] = useState('moved')
  const [sel, setSel] = useState<Set<string>>(new Set())
  const [retrain, setRetrain] = useState(true)

  const { data: board } = useQuery<DecisionBoard>({
    queryKey: ['decisions-board'],
    queryFn: () => pricingApi.decisionsBoard().then(r => r.data),
    refetchInterval: 30_000,
  })
  const { data: page } = useQuery<{ summary: Record<string, number | string>
                                    total: number; items: DecisionItem[] }>({
    queryKey: ['decisions-items', verdict],
    queryFn: () => pricingApi.decisionsItems({ verdict, limit: 200 }).then(r => r.data),
  })

  const toggle = (id: string) =>
    setSel(s => { const n = new Set(s); n.has(id) ? n.delete(id) : n.add(id); return n })

  const rescore = async () => {
    setBusy(true)
    try {
      const { data } = await pricingApi.decisionsRescore({})
      const s = data.summary || {}
      onMsg({ kind: 'ok', text: s.total
        ? t(`${n(s.total)} decisions re-tested — ${n(s.disagreements)} disagreements: `
            + `${n(s.moved)} moved, ${n(s.lost)} inconclusive, ${n(s.stale_target)} target removed, ${n(s.revived)} rejections worth revisiting.`,
            `${n(s.total)} تصمیم دوباره سنجیده شد — ${n(s.disagreements)} اختلاف: `
            + `${n(s.moved)} جابه‌جا، ${n(s.lost)} بی‌نتیجه، ${n(s.stale_target)} مقصد حذف‌شده، ${n(s.revived)} رد قابل بازنگری.`)
        : t('There is no decision to test.', 'تصمیمی برای سنجش وجود ندارد.') })
      qc.invalidateQueries({ queryKey: ['decisions-items'] })
      qc.invalidateQueries({ queryKey: ['decisions-board'] })
    } catch (e) { onError(e, t('The revision pass failed.', 'بازبینی ناموفق بود.')) } finally { setBusy(false) }
  }

  const backfill = async () => {
    setBusy(true)
    try {
      const { data } = await pricingApi.decisionsBackfill({})
      const count = Object.values(data.from_snapshots || {})
        .concat(Object.values(data.derived || {}))
        .reduce((a: number, r: any) => a + (r?.auto_created || 0), 0)
      onMsg({ kind: 'ok', text: t(`${n(count)} engine auto-links recorded — from now on any change to them is visible.`,
                                  `${n(count)} پیوند خودکار موتور ثبت شد — از این پس هر تغییرشان دیده می‌شود.`) })
      qc.invalidateQueries({ queryKey: ['decisions-board'] })
    } catch (e) { onError(e, t('Recording the auto-links failed.', 'ثبت پیوندهای خودکار ناموفق بود.')) } finally { setBusy(false) }
  }

  const act = async (action: string, ids?: string[]) => {
    const target = ids || [...sel]
    if (!target.length) return
    if (action === 'reopen' && !window.confirm(
      t(`Delete ${n(target.length)} decisions and send them back to the review queue?`,
        `${n(target.length)} تصمیم حذف و به صف بازبینی برگردانده شود؟`))) return
    setBusy(true)
    try {
      const { data } = await pricingApi.decisionsRevise({ ids: target, action, retrain })
      const trained = data.retrained
        ? t(` · model retrained on ${n(data.retrained.counts?.owner_decisions ?? 0)} owner rulings`,
            ` · مدل با ${n(data.retrained.counts?.owner_decisions ?? 0)} رأی مالک بازآموزی شد`)
        : ''
      onMsg({ kind: 'ok', text: t(`${n(data.changed)} decisions updated${trained}`,
                                  `${n(data.changed)} تصمیم به‌روزرسانی شد${trained}`) })
      setSel(new Set())
      qc.invalidateQueries({ queryKey: ['decisions-items'] })
      qc.invalidateQueries({ queryKey: ['decisions-board'] })
    } catch (e) { onError(e, t('Applying the revision failed.', 'اعمال بازبینی ناموفق بود.')) } finally { setBusy(false) }
  }

  const items = page?.items || []
  return (
    <div className="bg-slate-800/50 border border-fuchsia-500/30 rounded-lg p-4 space-y-3">
      <div className="flex items-baseline gap-2 flex-wrap">
        <h3 className="font-bold text-fuchsia-200">🔄 {t('Decision revision', 'بازبینی تصمیم‌ها')}</h3>
        <span className="text-[12px] text-slate-400">
          {t('Today’s engine answers the same past questions again; only where it changed its mind is shown.',
             'موتور امروز همان پرسش‌های گذشته را دوباره پاسخ می‌دهد؛ فقط جاهایی که نظرش عوض شده نمایش داده می‌شود.')}
        </span>
        <span className="mr-auto flex flex-wrap gap-2">
          <button onClick={rescore} disabled={busy}
            title={t('Nothing changes — it only measures', 'هیچ چیز تغییر نمی‌کند — فقط سنجیده می‌شود')}
            className="px-3 py-1 text-[12px] rounded bg-fuchsia-700 hover:bg-fuchsia-600 disabled:opacity-40">
            🔄 {t('Re-test every decision', 'بازبینی همهٔ تصمیم‌ها')}
          </button>
          <button onClick={backfill} disabled={busy}
            title={t('Links the engine applied itself are not recorded yet; without them the board is nearly empty',
                     'پیوندهایی که موتور خودش اعمال کرده هنوز ثبت نشده‌اند؛ بدون آن‌ها تابلو تقریباً خالی است')}
            className="px-3 py-1 text-[12px] rounded bg-slate-700 hover:bg-slate-600 disabled:opacity-40">
            {t('Record existing auto-links', 'ثبت پیوندهای خودکار موجود')}
          </button>
        </span>
      </div>

      <div className="flex flex-wrap gap-2 text-[11px]">
        <Chip label={t('All decisions', 'کل تصمیم‌ها')} value={n(board?.total ?? 0)} />
        <Chip label={t('Owner rulings', 'رأی مالک')} value={n(board?.by_origin?.owner ?? 0)} />
        <Chip label={t('Engine beliefs', 'باور موتور')} value={n(board?.by_origin?.auto ?? 0)} />
        <Chip label={t('Revised', 'بازبینی‌شده')} value={n(board?.revised ?? 0)} />
        {board?.last_pass && <Chip label={t('Disagreements in the last pass', 'اختلاف در آخرین سنجش')}
          value={n(Number(board.last_pass.disagreements ?? 0))} />}
      </div>

      <div className="flex flex-wrap items-center gap-2 text-[12px]">
        {Object.keys(REVISION_VERDICT).map(v => (
          <button key={v} onClick={() => { setVerdict(v); setSel(new Set()) }}
            className={`px-2 py-0.5 rounded border ${verdict === v
              ? VERDICT_TONE[v] : 'bg-slate-900 border-slate-700 text-slate-400'}`}>
            {REVISION_VERDICT[v] ? tp(REVISION_VERDICT[v]) : v}
            {board?.last_pass ? ` (${fa(Number(board.last_pass[v] ?? 0))})` : ''}
          </button>))}
        <label className="flex items-center gap-1 text-slate-400 mr-auto">
          <input type="checkbox" checked={retrain} onChange={e => setRetrain(e.target.checked)} />
          {t('Retrain the model after each ruling', 'بازآموزی مدل پس از هر رأی')}
        </label>
      </div>

      {items.length > 0 && (
        <div className="flex flex-wrap items-center gap-2 text-[12px]">
          <button onClick={() => setSel(new Set(items.map(i => i.id)))}
            className="px-2 py-0.5 rounded bg-slate-700 hover:bg-slate-600">{t('Select all', 'انتخاب همه')}</button>
          <span className="text-slate-400">{t(`${n(sel.size)} selected of ${n(page?.total ?? 0)}`,
                                              `${n(sel.size)} انتخاب‌شده از ${n(page?.total ?? 0)}`)}</span>
          <button onClick={() => act('keep')} disabled={busy || !sel.size}
            title={t('The recorded decision is right — your ruling is stored as a training label',
                     'تصمیم ثبت‌شده درست است — رأی شما به‌عنوان برچسب آموزشی ثبت می‌شود')}
            className="px-2 py-0.5 rounded bg-emerald-700 hover:bg-emerald-600 disabled:opacity-40">
            {t('Keep the earlier decision', 'تأیید تصمیم قبلی')}</button>
          <button onClick={() => act('accept_engine')} disabled={busy || !sel.size}
            className="px-2 py-0.5 rounded bg-amber-700 hover:bg-amber-600 disabled:opacity-40">
            {t('Accept the engine’s view', 'پذیرش نظر موتور')}</button>
          <button onClick={() => act('reject')} disabled={busy || !sel.size}
            className="px-2 py-0.5 rounded bg-red-800 hover:bg-red-700 disabled:opacity-40">
            {t('Reject this match', 'رد این تطبیق')}</button>
          <button onClick={() => act('reopen')} disabled={busy || !sel.size}
            className="px-2 py-0.5 rounded bg-slate-700 hover:bg-slate-600 disabled:opacity-40">
            {t('Back to the review queue', 'بازگشت به صف بازبینی')}</button>
        </div>
      )}

      {items.length === 0
        ? <Empty text={board?.last_pass
            ? t('No disagreement in this bucket.', 'در این دسته اختلافی نیست.')
            : t('Press “Re-test every decision” first so the engine measures again.',
                'ابتدا «بازبینی همهٔ تصمیم‌ها» را بزنید تا موتور دوباره بسنجد.')} />
        : <div className="space-y-1.5">
            {items.map(it => (
              <div key={it.id} className="flex flex-wrap items-start gap-x-3 gap-y-1 text-[12px] border border-slate-700/60 rounded-md p-2">
                <input type="checkbox" checked={sel.has(it.id)} onChange={() => toggle(it.id)} className="mt-1" />
                <div className="flex-1 min-w-[16rem] space-y-1">
                  <div className="flex flex-wrap items-center gap-2">
                    <span className="font-semibold text-slate-100">{it.raw_name}</span>
                    {it.code && <span className="font-mono text-[11px] text-slate-500">{t('code', 'کد')} {it.code}</span>}
                    <span className="text-[10px] px-1.5 py-0.5 rounded bg-slate-700 text-slate-300">
                      {INSURER_FA[it.insurer] || it.insurer}</span>
                    <span className={`text-[10px] px-1.5 py-0.5 rounded border ${VERDICT_TONE[it.verdict]}`}>
                      {REVISION_VERDICT[it.verdict] ? tp(REVISION_VERDICT[it.verdict]) : it.verdict}</span>
                    <span className="text-[10px] px-1.5 py-0.5 rounded bg-slate-900 border border-slate-700 text-slate-400">
                      {it.origin === 'owner' ? t('owner ruling', 'رأی مالک') : t('engine belief', 'باور موتور')}</span>
                  </div>
                  <div className="grid md:grid-cols-2 gap-2">
                    <SideCard title={t('Recorded decision', 'تصمیم ثبت‌شده')} tone="amber" side={it.stored} />
                    <SideCard title={t('Engine’s view today', 'نظر موتور امروز')} tone="sky" side={it.engine} />
                  </div>
                </div>
              </div>))}
          </div>}
    </div>
  )
}

function SideCard({ title, tone, side }: { title: string; tone: 'amber' | 'sky'; side: DecisionSide }) {
  const { t, n } = useLang()
  const border = tone === 'amber' ? 'border-amber-500/30 bg-amber-500/5' : 'border-sky-500/30 bg-sky-500/5'
  const label = tone === 'amber' ? 'text-amber-300/80' : 'text-sky-300/80'
  return (
    <div className={`rounded border p-2 space-y-0.5 ${border}`}>
      <p className={`text-[10px] ${label}`}>{title}</p>
      {!side.irc
        ? <p className="text-[11px] text-slate-600">—</p>
        : <>
            <p className="text-[11px] text-slate-200">
              {side.name_fa || side.generic || side.irc}
              {!side.exists && <span className="ms-2 text-red-400">{t('(not in the catalog)', '(در کاتالوگ نیست)')}</span>}
            </p>
            <div className="flex flex-wrap gap-x-2 text-[11px] text-slate-500 tabular-nums">
              <span className="font-mono">{side.irc}</span>
              {side.strength && <span>{side.strength}</span>}
              {side.dosage_form && <span>{side.dosage_form}</span>}
              {side.price != null && <span>{n(side.price)} {t('IRR', '﷼')}</span>}
              {side.confidence != null && <span>{t('confidence', 'اطمینان')} {side.confidence}</span>}
              {side.method && <span className="text-slate-600">{side.method}</span>}
              {side.fs != null && <span className={side.fs < 0 ? 'text-red-400' : 'text-emerald-400'}>FS {side.fs}</span>}
            </div>
          </>}
    </div>
  )
}

function Chip({ label, value }: { label: string; value: string }) {
  return (
    <span className="px-2 py-0.5 rounded-full bg-slate-900 border border-slate-700 text-slate-300">
      {label}: <span className="font-mono">{value}</span>
    </span>
  )
}

// ── جانشینی IRC ─────────────────────────────────────────────────────────────
// A product re-registered under a new IRC arrives empty while the old row keeps
// every decision. Evidence is the /NFI/Detail page id: one page = one
// registration, so the same page serving a different IRC on a later audit pass
// is a re-registration and nothing else.
interface SuccessionRow {
  id: string; page_id: number | null; confidence: number | null
  evidence?: { url?: string; source?: string }
  old: { irc: string; exists: boolean; name_fa?: string | null; coverage: string[] }
  new: { irc: string; exists: boolean; name_fa?: string | null; coverage: string[] }
}

function SuccessionPanel({ onMsg, onError }: {
  onMsg: (m: { kind: 'ok' | 'err'; text: string }) => void
  onError: (e: unknown, fallback: string) => void
}) {
  const { t, n } = useLang()
  const qc = useQueryClient()
  const [busy, setBusy] = useState(false)
  const { data } = useQuery<{ pending: SuccessionRow[] }>({
    queryKey: ['succession'],
    queryFn: () => pricingApi.successionList().then(r => r.data),
  })
  const rows = data?.pending || []

  const scan = async () => {
    setBusy(true)
    try {
      const { data } = await pricingApi.successionScan()
      onMsg({ kind: 'ok', text: data.detected
        ? t(`${n(data.detected)} successions found (${n(data.created)} new).`,
            `${n(data.detected)} جانشینی یافت شد (${n(data.created)} تازه).`)
        : t('No evidence of a registration change — a page has to be scanned again for that (audit scan with “re-scan”).',
            'شواهدی از تغییر ثبت یافت نشد — برای این کار باید صفحه‌ای دوباره پویش شود (پویش ممیزی با گزینهٔ «پویش دوباره»).') })
      qc.invalidateQueries({ queryKey: ['succession'] })
    } catch (e) { onError(e, t('The succession scan failed.', 'پویش جانشینی ناموفق بود.')) } finally { setBusy(false) }
  }
  const decide = async (id: string, apply: boolean) => {
    setBusy(true)
    try {
      if (apply) {
        const { data } = await pricingApi.successionApply([id])
        onMsg({ kind: 'ok', text: t(`${n(data.applied)} successions applied — coverage, corrections and mappings were carried over.`,
                                    `${n(data.applied)} جانشینی اعمال شد — پوشش، اصلاح‌ها و نگاشت‌ها منتقل شدند.`) })
      } else {
        await pricingApi.successionDismiss([id])
        onMsg({ kind: 'ok', text: t('The proposal was dismissed.', 'پیشنهاد کنار گذاشته شد.') })
      }
      qc.invalidateQueries({ queryKey: ['succession'] })
    } catch (e) { onError(e, t('Applying failed.', 'اعمال ناموفق بود.')) } finally { setBusy(false) }
  }

  return (
    <div className="bg-slate-800/50 border border-cyan-500/30 rounded-lg p-4 space-y-2">
      <div className="flex items-baseline gap-2 flex-wrap">
        <h3 className="font-bold text-cyan-200">🧬 {t('IRC succession', 'جانشینی IRC')}</h3>
        <span className="text-[12px] text-slate-400">
          {t('When a product is re-registered under a new IRC, its coverage and your corrections must travel with it.',
             'وقتی محصولی با IRC تازه ثبت می‌شود، پوشش بیمه و اصلاح‌های شما باید همراهش بروند.')}
        </span>
        <button onClick={scan} disabled={busy}
          className="mr-auto px-3 py-1 text-[12px] rounded bg-cyan-800 hover:bg-cyan-700 disabled:opacity-40">
          {t('Scan for evidence', 'پویش شواهد')}
        </button>
      </div>
      {rows.length === 0
        ? <Empty text={t('No proposal. Evidence comes from comparing two audit scans of the same page.',
                          'پیشنهادی نیست. شواهد از مقایسهٔ دو پویش ممیزی از یک صفحه به دست می‌آید.')} />
        : <div className="space-y-1.5">
            {rows.map(r => (
              <div key={r.id} className="flex flex-wrap items-center gap-x-3 gap-y-1 text-[12px] border border-slate-700/60 rounded-md p-2">
                <div className="flex-1 min-w-[18rem]">
                  <div className="text-slate-200">
                    <span className="font-mono">{r.old.irc}</span>
                    {r.old.name_fa && <span className="text-slate-500"> ({r.old.name_fa})</span>}
                    <span className="mx-2 text-cyan-300">←</span>
                    <span className="font-mono">{r.new.irc}</span>
                    {r.new.name_fa && <span className="text-slate-500"> ({r.new.name_fa})</span>}
                  </div>
                  <div className="flex flex-wrap gap-x-3 text-[11px] text-slate-500">
                    {r.page_id != null && r.evidence?.url && (
                      <a href={r.evidence.url} target="_blank" rel="noreferrer"
                        className="text-indigo-300 hover:underline">{t('page', 'صفحهٔ')} {n(r.page_id)}↗</a>)}
                    {r.old.coverage.length > 0 && <span>{t('transferable coverage', 'پوشش قابل انتقال')}: {r.old.coverage.join(t(', ', '، '))}</span>}
                    {!r.new.exists && <span className="text-red-400">{t('the successor is not in the catalog', 'جانشین در کاتالوگ نیست')}</span>}
                  </div>
                </div>
                <button onClick={() => decide(r.id, true)} disabled={busy || !r.new.exists}
                  className="px-2 py-0.5 rounded bg-emerald-700 hover:bg-emerald-600 disabled:opacity-40">{t('Transfer', 'انتقال')}</button>
                <button onClick={() => decide(r.id, false)} disabled={busy}
                  className="px-2 py-0.5 rounded bg-slate-700 hover:bg-slate-600 disabled:opacity-40">{t('Dismiss', 'نادیده')}</button>
              </div>))}
          </div>}
    </div>
  )
}
