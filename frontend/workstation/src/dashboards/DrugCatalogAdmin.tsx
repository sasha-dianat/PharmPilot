/**
 * DrugCatalogAdmin — manage the IRC drug catalog: live NFI harvest, file import,
 * and catalog stats. The harvest crawls irc.fda.gov.ir/NFI/Detail/{id} in the
 * background (needs an Iran proxy) and upserts products as they arrive.
 */
import { useRef, useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { pricingApi, apiErrorText } from '../lib/api'

interface Stats { total: number; priced: number; ingredient_groups: number; last_updated: string | null }
interface HarvestStatus {
  running: boolean; scanned: number; products: number; ingested: number
  quarantined?: number; flagged?: number; mode?: 'ingest' | 'audit'
  last_id: number; start_id: number; end_id: number; progress_pct: number
  elapsed_sec: number; eta_sec: number | null; message: string; error: string | null
  diagnostics?: { failed: number; worst_category: string | null; top_hint: string | null }
  // set only while a stopped run has ground left to cover
  resume?: { resume_from: number; end_id: number; mode: 'ingest' | 'audit'
             source: string; saved_at: string } | null
}
interface AuditSummary {
  pages: number; flagged: number; failed: number
  by_flag: Record<string, number>; index: string; irc_map: string
}
const FLAG_FA: Record<string, string> = {
  no_irc: 'بدون IRC', no_country: 'بدون کشور', no_price: 'بدون قیمت',
  no_strength: 'بدون دوز', no_atc: 'بدون ATC', no_generic: 'بدون ژنریک',
  no_brands_table: 'بدون جدول محصولات مشابه',
  section_similar_absent: 'بخش «محصولات مشابه» در منبع نیست',
  section_price_absent: 'بخش قیمت در منبع نیست',
  spliced: 'صفحهٔ دوپاره', unparsable: 'غیرقابل تجزیه',
}

const fa = (n: number) => new Intl.NumberFormat('fa-IR').format(n)
const hms = (s: number | null) => {
  if (s == null) return '—'
  const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), sec = Math.floor(s % 60)
  return h ? `${h}س ${m}د` : m ? `${m}د ${sec}ث` : `${sec}ث`
}

export default function DrugCatalogAdmin() {
  const qc = useQueryClient()
  const fileRef = useRef<HTMLInputElement>(null)
  const [proxy, setProxy] = useState('')
  const [startId, setStartId] = useState(1)
  const [endId, setEndId] = useState(60000)
  const [delay, setDelay] = useState(0.25)
  const [mode, setMode] = useState<'ingest' | 'audit'>('ingest')
  const [force, setForce] = useState(false)
  const [msg, setMsg] = useState<{ kind: 'ok' | 'err'; text: string } | null>(null)
  const [busy, setBusy] = useState(false)

  const { data: stats } = useQuery<Stats>({
    queryKey: ['catalog-stats'],
    queryFn: () => pricingApi.catalogStats().then(r => r.data),
    refetchInterval: 5_000,
  })
  const { data: hs } = useQuery<HarvestStatus>({
    queryKey: ['nfi-status'],
    queryFn: () => pricingApi.nfiStatus().then(r => r.data),
    refetchInterval: q => (q.state.data?.running ? 2_000 : 15_000),
  })
  const running = hs?.running

  const start = async () => {
    setBusy(true); setMsg(null)
    try {
      await pricingApi.nfiStart({ start_id: startId, end_id: endId, delay,
                                 proxy: proxy || undefined, mode, force })
      qc.invalidateQueries({ queryKey: ['nfi-status'] })
    } catch (e: unknown) {
      setMsg({ kind: 'err', text: apiErrorText(e, 'شروع برداشت ناموفق بود.') })
    } finally { setBusy(false) }
  }
  const stop = async () => { await pricingApi.nfiStop(); qc.invalidateQueries({ queryKey: ['nfi-status'] }) }
  // a manually stopped run leaves its position on disk, so it can be picked up
  // again later — even after the backend restarts
  const resume = async () => {
    setBusy(true); setMsg(null)
    try {
      await pricingApi.nfiResume({ proxy: proxy || undefined, delay })
      qc.invalidateQueries({ queryKey: ['nfi-status'] })
    } catch (e: unknown) {
      setMsg({ kind: 'err', text: apiErrorText(e, 'ادامهٔ برداشت ناموفق بود.') })
    } finally { setBusy(false) }
  }

  // pages previous passes lost to transport failures — retrying only those
  // avoids re-sweeping ~70,000 ids to recover a few thousand
  const { data: fails } = useQuery<{ pending: number; retryable: number; resolved: number
                                     by_category: { category: string; count: number
                                       min_id: number; max_id: number; retryable: boolean }[] }>({
    queryKey: ['nfi-failures'],
    queryFn: () => pricingApi.nfiFailures().then(r => r.data),
    refetchInterval: () => (hs?.running ? 10_000 : 60_000),
  })
  const retryFailed = async () => {
    setBusy(true); setMsg(null)
    try {
      const { data } = await pricingApi.nfiRetryFailed({ proxy: proxy || undefined, delay })
      setMsg({ kind: 'ok', text: `تلاش دوباره روی ${fa(data.retrying)} صفحهٔ ناموفق آغاز شد.` })
      qc.invalidateQueries({ queryKey: ['nfi-status'] })
    } catch (e: unknown) {
      setMsg({ kind: 'err', text: apiErrorText(e, 'تلاش دوباره ناموفق بود.') })
    } finally { setBusy(false) }
  }

  // audit-mode output lives on disk, not in the catalog — surface its shape so a
  // finished pass is readable without opening logs/nfi_audit/index.jsonl
  const { data: audit } = useQuery<AuditSummary>({
    queryKey: ['nfi-audit-summary'],
    queryFn: () => pricingApi.nfiAuditSummary().then(r => r.data),
    refetchInterval: () => (hs?.running && hs?.mode === 'audit' ? 5_000 : 60_000),
  })

  const importCatalog = async (file: File) => {
    setBusy(true); setMsg(null)
    try {
      const { data } = await pricingApi.importCatalog(file)
      setMsg({ kind: 'ok', text: `${fa(data.imported)} قلم از فایل وارد شد.` })
      qc.invalidateQueries({ queryKey: ['catalog-stats'] })
    } catch (e: unknown) {
      setMsg({ kind: 'err', text: apiErrorText(e, 'بارگذاری فایل ناموفق بود.') })
    } finally { setBusy(false); if (fileRef.current) fileRef.current.value = '' }
  }

  return (
    <div className="p-4 space-y-4 text-slate-100" dir="rtl">
      <h2 className="text-lg font-bold">کاتالوگ دارو — به‌روزرسانی از سامانه اطلاعات دارویی (NFI)</h2>
      {msg && <p className={`text-sm ${msg.kind === 'ok' ? 'text-emerald-400' : 'text-red-400'}`}>{msg.text}</p>}

      {/* ── Catalog stats ─────────────────────────────────────────────────── */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
        <Stat label="اقلام کاتالوگ" value={stats ? fa(stats.total) : '—'} />
        <Stat label="دارای قیمت" value={stats ? fa(stats.priced) : '—'} />
        <Stat label="گروه‌های ماده مؤثره" value={stats ? fa(stats.ingredient_groups) : '—'} />
        <Stat label="آخرین به‌روزرسانی" value={stats?.last_updated ? new Date(stats.last_updated).toLocaleString('fa-IR') : '—'} small />
      </div>

      {/* ── NFI harvest ───────────────────────────────────────────────────── */}
      <div className="bg-slate-800/50 border border-slate-700 rounded-lg p-4 space-y-3">
        <div className="flex items-center gap-2">
          <span className="font-semibold text-sm">برداشت خودکار از NFI</span>
          <span className={`text-[11px] px-2 py-0.5 rounded-full ${running ? 'bg-emerald-500/15 text-emerald-300' : 'bg-slate-700 text-slate-400'}`}>
            {running ? 'در حال اجرا' : 'آماده'}
          </span>
          <span className="text-[11px] text-slate-500">نیازمند پروکسی ایران — کل پایگاه از طریق صفحات محصول برداشت می‌شود</span>
        </div>

        <div className="flex flex-wrap items-center gap-2 text-[12px]">
          {([['ingest', 'برداشت به کاتالوگ', 'صفحات خوانده و در کاتالوگ ثبت می‌شوند'],
             ['audit', 'پویش ممیزی', 'چیزی در کاتالوگ نوشته نمی‌شود؛ منبع صفحات مشکوک برای بررسی ذخیره می‌شود']] as const)
            .map(([m, label, hint]) => (
            <button key={m} onClick={() => setMode(m)} disabled={running} title={hint}
              className={`px-3 py-1 rounded-lg border transition-colors disabled:opacity-50 ${
                mode === m ? 'bg-indigo-600/25 border-indigo-500 text-indigo-200'
                           : 'bg-slate-900 border-slate-700 text-slate-400 hover:border-slate-500'}`}>
              {label}
            </button>))}
          {mode === 'audit' && (
            <label className="flex items-center gap-1 text-[11px] text-slate-400"
              title="صفحات پویش‌شده را دوباره می‌خواند. تنها راه دیدن اینکه IRC یک صفحه عوض شده — یعنی همان شواهدِ جانشینی">
              <input type="checkbox" checked={force} disabled={running}
                onChange={e => setForce(e.target.checked)} />
              پویش دوباره (صرف‌نظر از صفحات پویش‌شده)
            </label>)}
          <span className="text-[11px] text-slate-500">
            {mode === 'audit'
              ? 'فقط بررسی — منبع خام صفحات پرچم‌خورده در logs/nfi_audit ذخیره می‌شود'
              : 'صفحات خوانده و مستقیماً در کاتالوگ ثبت می‌شوند'}
          </span>
        </div>

        <div className="flex flex-wrap items-end gap-3 text-[12px]">
          <Field label="پروکسی (اختیاری — یا HTTPS_PROXY سرور)">
            <input value={proxy} onChange={e => setProxy(e.target.value)} placeholder="http://host:port"
              disabled={running} className="w-56 bg-slate-900 border border-slate-600 rounded px-2 py-1 disabled:opacity-50" />
          </Field>
          <Field label="از شناسه"><input type="number" value={startId} disabled={running}
            onChange={e => setStartId(+e.target.value)} className="w-24 bg-slate-900 border border-slate-600 rounded px-2 py-1 disabled:opacity-50" /></Field>
          <Field label="تا شناسه"><input type="number" value={endId} disabled={running}
            onChange={e => setEndId(+e.target.value)} className="w-24 bg-slate-900 border border-slate-600 rounded px-2 py-1 disabled:opacity-50" /></Field>
          <Field label="مکث (ثانیه)"><input type="number" step="0.05" value={delay} disabled={running}
            onChange={e => setDelay(+e.target.value)} className="w-20 bg-slate-900 border border-slate-600 rounded px-2 py-1 disabled:opacity-50" /></Field>
          {running
            ? <button onClick={stop} className="px-4 py-1.5 bg-red-600 hover:bg-red-500 rounded-lg">توقف</button>
            : <button onClick={start} disabled={busy} className="px-4 py-1.5 bg-emerald-600 hover:bg-emerald-500 rounded-lg disabled:opacity-50">
                {mode === 'audit' ? 'شروع پویش' : 'شروع برداشت'}</button>}
          {!running && hs?.resume && (
            <button onClick={resume} disabled={busy}
              title={`${hs.resume.mode === 'audit' ? 'پویش ممیزی' : 'برداشت'} متوقف‌شده — `
                + `تا شناسه ${hs.resume.end_id} · ذخیره در ${new Date(hs.resume.saved_at).toLocaleString('fa-IR')}`}
              className="px-4 py-1.5 bg-indigo-600 hover:bg-indigo-500 rounded-lg disabled:opacity-50">
              ▶ ادامه از شناسهٔ {fa(hs.resume.resume_from)}
            </button>)}
          {!running && (fails?.retryable ?? 0) > 0 && (
            <button onClick={retryFailed} disabled={busy}
              title={(fails?.by_category || []).filter(c => c.retryable)
                .map(c => `${c.category}: ${c.count} (${c.min_id}–${c.max_id})`).join(' · ')}
              className="px-4 py-1.5 bg-amber-700 hover:bg-amber-600 rounded-lg disabled:opacity-50">
              ↻ تلاش دوباره روی {fa(fails!.retryable)} صفحهٔ ناموفق
            </button>)}
        </div>

        {hs && (hs.running || hs.scanned > 0) && (
          <div className="space-y-1.5">
            <div className="h-2 bg-slate-700 rounded-full overflow-hidden">
              <div className="h-full bg-emerald-500 transition-all" style={{ width: `${Math.min(100, hs.progress_pct)}%` }} />
            </div>
            <div className="flex flex-wrap gap-x-5 gap-y-1 text-[11px] text-slate-400 font-mono">
              <span>{hs.progress_pct}٪</span>
              {hs.mode === 'audit' && <span className="text-indigo-300">حالت: پویش ممیزی</span>}
              <span>پیموده‌شده: {fa(hs.scanned)}</span>
              <span className="text-emerald-300">محصولات: {fa(hs.products)}</span>
              {hs.mode === 'audit'
                ? <span className="text-amber-300" title="صفحاتی که منبع خامشان برای بررسی ذخیره شد">
                    پرچم‌خورده: {fa(hs.flagged ?? 0)}</span>
                : <span className="text-indigo-300">ثبت‌شده: {fa(hs.ingested)}</span>}
              {(hs.quarantined ?? 0) > 0 && (
                <span className="text-rose-300"
                  title="صفحات دوپاره (مونوگراف دارویی دیگر) — بلوک محصول حفظ شد، مونوگراف بیگانه وارد نشد">
                  🧬 قرنطینه: {fa(hs.quarantined!)}</span>)}
              <span>شناسه فعلی: {fa(hs.last_id)}</span>
              <span>سپری‌شده: {hms(hs.elapsed_sec)}</span>
              <span>باقی‌مانده: {hms(hs.eta_sec)}</span>
              <span className={hs.error ? 'text-red-400' : ''}>{hs.error || hs.message}</span>
              {hs.diagnostics && hs.diagnostics.failed > 0 && (
                <span className="text-amber-300">تشخیص: {hs.diagnostics.worst_category} — {hs.diagnostics.top_hint}</span>
              )}
            </div>
          </div>
        )}
      </div>

      {/* ── Audit findings ────────────────────────────────────────────────── */}
      {(audit?.pages ?? 0) > 0 && (
        <div className="bg-slate-800/50 border border-slate-700 rounded-lg p-4 space-y-2">
          <div className="flex flex-wrap items-center gap-3">
            <span className="font-semibold text-sm">یافته‌های پویش ممیزی</span>
            <span className="text-[11px] text-slate-400 font-mono">
              صفحات: {fa(audit!.pages)} · پرچم‌خورده: {fa(audit!.flagged)} · ناموفق: {fa(audit!.failed)}
            </span>
          </div>
          <button onClick={async () => {
            setBusy(true); setMsg(null)
            try {
              const { data } = await pricingApi.nfiBackfillPageIds()
              setMsg({ kind: 'ok', text: `شناسهٔ صفحه روی ${fa(data.updated)} قلم کاتالوگ ثبت شد `
                + `(${fa(data.mapped)} نگاشت، ${fa(data.missing)} بدون قلم متناظر).` })
            } catch (e: unknown) {
              setMsg({ kind: 'err', text: apiErrorText(e, 'ثبت شناسهٔ صفحه ناموفق بود.') })
            } finally { setBusy(false) }
          }} disabled={busy}
            title="هر قلم کاتالوگ را به صفحهٔ مبدأ خودش وصل می‌کند — بدون آن هیچ ردیف مشکوکی قابل ردیابی نیست"
            className="px-3 py-1 text-[12px] rounded bg-indigo-700 hover:bg-indigo-600 disabled:opacity-50">
            ثبت شناسهٔ صفحه روی اقلام کاتالوگ
          </button>
          <p className="text-[11px] text-slate-500">
            منبع خام صفحات پرچم‌خورده در <code className="font-mono">{audit!.index.replace('index.jsonl', 'pages/')}</code> ذخیره شده است —
            نبودِ یک بخش در منبع یعنی داده اصلاً منتشر نشده، نه اینکه ما نخوانده‌ایم.
          </p>
          <div className="flex flex-wrap gap-2">
            {Object.entries(audit!.by_flag).map(([flag, n]) => (
              <span key={flag} className="text-[11px] px-2 py-0.5 rounded-full bg-slate-900 border border-slate-700 text-slate-300">
                {FLAG_FA[flag] || flag}: <span className="font-mono">{fa(n)}</span>
              </span>))}
          </div>
        </div>
      )}

      {/* ── File import ───────────────────────────────────────────────────── */}
      <div className="bg-slate-800/50 border border-slate-700 rounded-lg p-4 space-y-2">
        <p className="font-semibold text-sm">بارگذاری فهرست رسمی (Excel / CSV)</p>
        <p className="text-[11px] text-slate-500">اگر خروجی رسمی سازمان غذا و دارو را دارید، مستقیماً بارگذاری کنید — ستون‌های فارسی/انگلیسی خودکار نگاشت می‌شوند.</p>
        <input ref={fileRef} type="file" accept=".xlsx,.xls,.csv,.tsv" className="text-sm text-slate-300"
          onChange={e => { const f = e.target.files?.[0]; if (f) importCatalog(f) }} disabled={busy} />
      </div>
    </div>
  )
}

function Stat({ label, value, small }: { label: string; value: string; small?: boolean }) {
  return (
    <div className="bg-slate-800/50 border border-slate-700 rounded-lg px-3 py-2">
      <div className="text-[10px] text-slate-500">{label}</div>
      <div className={`font-bold ${small ? 'text-xs' : 'text-xl'} tabular-nums`}>{value}</div>
    </div>
  )
}
function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return <label className="flex flex-col gap-1"><span className="text-slate-500">{label}</span>{children}</label>
}
