/**
 * DrugCatalogAdmin — manage the IRC drug catalog: live NFI harvest, file import,
 * and catalog stats. The harvest crawls irc.fda.gov.ir/NFI/Detail/{id} in the
 * background (needs an Iran proxy) and upserts products as they arrive.
 */
import { useRef, useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { pricingApi, apiErrorText } from '../lib/api'
import { useLang } from '../lib/i18n'

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
const FLAG_LABELS: Record<string, readonly [string, string]> = {
  no_irc: ['No IRC', 'بدون IRC'], no_country: ['No country', 'بدون کشور'],
  no_price: ['No price', 'بدون قیمت'], no_strength: ['No strength', 'بدون دوز'],
  no_atc: ['No ATC', 'بدون ATC'], no_generic: ['No generic', 'بدون ژنریک'],
  no_brands_table: ['No similar-products table', 'بدون جدول محصولات مشابه'],
  section_similar_absent: ['“Similar products” section absent from source', 'بخش «محصولات مشابه» در منبع نیست'],
  section_price_absent: ['Price section absent from source', 'بخش قیمت در منبع نیست'],
  spliced: ['Spliced page', 'صفحهٔ دوپاره'], unparsable: ['Unparsable', 'غیرقابل تجزیه'],
}

const hms = (s: number | null, fa: boolean) => {
  if (s == null) return '—'
  const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), sec = Math.floor(s % 60)
  const u = fa ? ['س', 'د', 'ث'] : ['h', 'm', 's']
  return h ? `${h}${u[0]} ${m}${u[1]}` : m ? `${m}${u[1]} ${sec}${u[2]}` : `${sec}${u[2]}`
}

export default function DrugCatalogAdmin() {
  const { t, tp, n: fa, dir, dateTime, lang } = useLang()
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
      setMsg({ kind: 'err', text: apiErrorText(e, t('Could not start the harvest.', 'شروع برداشت ناموفق بود.')) })
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
      setMsg({ kind: 'err', text: apiErrorText(e, t('Could not resume the harvest.', 'ادامهٔ برداشت ناموفق بود.')) })
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
      setMsg({ kind: 'ok', text: t(`Retrying ${fa(data.retrying)} failed pages.`,
                                   `تلاش دوباره روی ${fa(data.retrying)} صفحهٔ ناموفق آغاز شد.`) })
      qc.invalidateQueries({ queryKey: ['nfi-status'] })
    } catch (e: unknown) {
      setMsg({ kind: 'err', text: apiErrorText(e, t('Retry failed.', 'تلاش دوباره ناموفق بود.')) })
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
      setMsg({ kind: 'ok', text: t(`Imported ${fa(data.imported)} items from the file.`,
                                   `${fa(data.imported)} قلم از فایل وارد شد.`) })
      qc.invalidateQueries({ queryKey: ['catalog-stats'] })
    } catch (e: unknown) {
      setMsg({ kind: 'err', text: apiErrorText(e, t('File upload failed.', 'بارگذاری فایل ناموفق بود.')) })
    } finally { setBusy(false); if (fileRef.current) fileRef.current.value = '' }
  }

  return (
    <div className="p-4 space-y-4 text-slate-100" dir={dir}>
      <h2 className="text-lg font-bold">{t('Drug catalog — updates from the National Formulary (NFI)',
                                           'کاتالوگ دارو — به‌روزرسانی از سامانه اطلاعات دارویی (NFI)')}</h2>
      {msg && <p className={`text-sm ${msg.kind === 'ok' ? 'text-emerald-400' : 'text-red-400'}`}>{msg.text}</p>}

      {/* ── Catalog stats ─────────────────────────────────────────────────── */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
        <Stat label={t('Catalog items', 'اقلام کاتالوگ')} value={stats ? fa(stats.total) : '—'} />
        <Stat label={t('Priced', 'دارای قیمت')} value={stats ? fa(stats.priced) : '—'} />
        <Stat label={t('Ingredient groups', 'گروه‌های ماده مؤثره')} value={stats ? fa(stats.ingredient_groups) : '—'} />
        <Stat label={t('Last updated', 'آخرین به‌روزرسانی')} value={dateTime(stats?.last_updated)} small />
      </div>

      {/* ── NFI harvest ───────────────────────────────────────────────────── */}
      <div className="bg-slate-800/50 border border-slate-700 rounded-lg p-4 space-y-3">
        <div className="flex items-center gap-2">
          <span className="font-semibold text-sm">{t('Automatic NFI harvest', 'برداشت خودکار از NFI')}</span>
          <span className={`text-[11px] px-2 py-0.5 rounded-full ${running ? 'bg-emerald-500/15 text-emerald-300' : 'bg-slate-700 text-slate-400'}`}>
            {running ? t('running', 'در حال اجرا') : t('idle', 'آماده')}
          </span>
          <span className="text-[11px] text-slate-500">{t('Needs an Iran proxy — the whole database is harvested through its product pages',
                                                              'نیازمند پروکسی ایران — کل پایگاه از طریق صفحات محصول برداشت می‌شود')}</span>
        </div>

        <div className="flex flex-wrap items-center gap-2 text-[12px]">
          {([['ingest', t('Harvest into catalog', 'برداشت به کاتالوگ'),
                        t('Pages are read and written into the catalog', 'صفحات خوانده و در کاتالوگ ثبت می‌شوند')],
             ['audit', t('Audit scan', 'پویش ممیزی'),
                       t('Nothing is written to the catalog; the source of suspicious pages is kept for review',
                         'چیزی در کاتالوگ نوشته نمی‌شود؛ منبع صفحات مشکوک برای بررسی ذخیره می‌شود')]] as const)
            .map(([m, label, hint]) => (
            <button key={m} onClick={() => setMode(m)} disabled={running} title={hint}
              className={`px-3 py-1 rounded-lg border transition-colors disabled:opacity-50 ${
                mode === m ? 'bg-indigo-600/25 border-indigo-500 text-indigo-200'
                           : 'bg-slate-900 border-slate-700 text-slate-400 hover:border-slate-500'}`}>
              {label}
            </button>))}
          {mode === 'audit' && (
            <label className="flex items-center gap-1 text-[11px] text-slate-400"
              title={t('Re-reads pages already scanned. The only way to see that a page\u2019s IRC changed — the substitution evidence itself',
                       'صفحات پویش‌شده را دوباره می‌خواند. تنها راه دیدن اینکه IRC یک صفحه عوض شده — یعنی همان شواهدِ جانشینی')}>
              <input type="checkbox" checked={force} disabled={running}
                onChange={e => setForce(e.target.checked)} />
              {t('Re-scan (ignore already-scanned pages)', 'پویش دوباره (صرف‌نظر از صفحات پویش‌شده)')}
            </label>)}
          <span className="text-[11px] text-slate-500">
            {mode === 'audit'
              ? t('Inspection only — the raw source of flagged pages is kept in logs/nfi_audit',
                  'فقط بررسی — منبع خام صفحات پرچم‌خورده در logs/nfi_audit ذخیره می‌شود')
              : t('Pages are read and written straight into the catalog',
                  'صفحات خوانده و مستقیماً در کاتالوگ ثبت می‌شوند')}
          </span>
        </div>

        <div className="flex flex-wrap items-end gap-3 text-[12px]">
          <Field label={t('Proxy (optional — or the server\u2019s HTTPS_PROXY)', 'پروکسی (اختیاری — یا HTTPS_PROXY سرور)')}>
            <input value={proxy} onChange={e => setProxy(e.target.value)} placeholder="http://host:port"
              disabled={running} className="w-56 bg-slate-900 border border-slate-600 rounded px-2 py-1 disabled:opacity-50" />
          </Field>
          <Field label={t('From id', 'از شناسه')}><input type="number" value={startId} disabled={running}
            onChange={e => setStartId(+e.target.value)} className="w-24 bg-slate-900 border border-slate-600 rounded px-2 py-1 disabled:opacity-50" /></Field>
          <Field label={t('To id', 'تا شناسه')}><input type="number" value={endId} disabled={running}
            onChange={e => setEndId(+e.target.value)} className="w-24 bg-slate-900 border border-slate-600 rounded px-2 py-1 disabled:opacity-50" /></Field>
          <Field label={t('Pause (seconds)', 'مکث (ثانیه)')}><input type="number" step="0.05" value={delay} disabled={running}
            onChange={e => setDelay(+e.target.value)} className="w-20 bg-slate-900 border border-slate-600 rounded px-2 py-1 disabled:opacity-50" /></Field>
          {running
            ? <button onClick={stop} className="px-4 py-1.5 bg-red-600 hover:bg-red-500 rounded-lg">{t('Stop', 'توقف')}</button>
            : <button onClick={start} disabled={busy} className="px-4 py-1.5 bg-emerald-600 hover:bg-emerald-500 rounded-lg disabled:opacity-50">
                {mode === 'audit' ? t('Start scan', 'شروع پویش') : t('Start harvest', 'شروع برداشت')}</button>}
          {!running && hs?.resume && (
            <button onClick={resume} disabled={busy}
              title={`${hs.resume.mode === 'audit' ? t('Audit scan', 'پویش ممیزی') : t('Harvest', 'برداشت')} `
                + `${t('stopped', 'متوقف‌شده')} — ${t('up to id', 'تا شناسه')} ${fa(hs.resume.end_id)} `
                + `· ${t('saved', 'ذخیره در')} ${dateTime(hs.resume.saved_at)}`}
              className="px-4 py-1.5 bg-indigo-600 hover:bg-indigo-500 rounded-lg disabled:opacity-50">
              ▶ {t('Resume from id', 'ادامه از شناسهٔ')} {fa(hs.resume.resume_from)}
            </button>)}
          {!running && (fails?.retryable ?? 0) > 0 && (
            <button onClick={retryFailed} disabled={busy}
              title={(fails?.by_category || []).filter(c => c.retryable)
                .map(c => `${c.category}: ${c.count} (${c.min_id}–${c.max_id})`).join(' · ')}
              className="px-4 py-1.5 bg-amber-700 hover:bg-amber-600 rounded-lg disabled:opacity-50">
              ↻ {t(`Retry ${fa(fails!.retryable)} failed pages`,
                     `تلاش دوباره روی ${fa(fails!.retryable)} صفحهٔ ناموفق`)}
            </button>)}
        </div>

        {hs && (hs.running || hs.scanned > 0) && (
          <div className="space-y-1.5">
            <div className="h-2 bg-slate-700 rounded-full overflow-hidden">
              <div className="h-full bg-emerald-500 transition-all" style={{ width: `${Math.min(100, hs.progress_pct)}%` }} />
            </div>
            <div className="flex flex-wrap gap-x-5 gap-y-1 text-[11px] text-slate-400 font-mono">
              <span>{fa(hs.progress_pct)}{t('%', '٪')}</span>
              {hs.mode === 'audit' && <span className="text-indigo-300">{t('mode: audit scan', 'حالت: پویش ممیزی')}</span>}
              <span>{t('scanned', 'پیموده‌شده')}: {fa(hs.scanned)}</span>
              <span className="text-emerald-300">{t('products', 'محصولات')}: {fa(hs.products)}</span>
              {hs.mode === 'audit'
                ? <span className="text-amber-300" title={t('Pages whose raw source was kept for review',
                                                            'صفحاتی که منبع خامشان برای بررسی ذخیره شد')}>
                    {t('flagged', 'پرچم‌خورده')}: {fa(hs.flagged ?? 0)}</span>
                : <span className="text-indigo-300">{t('ingested', 'ثبت‌شده')}: {fa(hs.ingested)}</span>}
              {(hs.quarantined ?? 0) > 0 && (
                <span className="text-rose-300"
                  title={t('Spliced pages (another drug\u2019s monograph) — the product block was kept, the foreign monograph was not imported',
                           'صفحات دوپاره (مونوگراف دارویی دیگر) — بلوک محصول حفظ شد، مونوگراف بیگانه وارد نشد')}>
                  🧬 {t('quarantined', 'قرنطینه')}: {fa(hs.quarantined!)}</span>)}
              <span>{t('current id', 'شناسه فعلی')}: {fa(hs.last_id)}</span>
              <span>{t('elapsed', 'سپری‌شده')}: {hms(hs.elapsed_sec, lang === 'fa')}</span>
              <span>{t('remaining', 'باقی‌مانده')}: {hms(hs.eta_sec, lang === 'fa')}</span>
              <span className={hs.error ? 'text-red-400' : ''}>{hs.error || hs.message}</span>
              {hs.diagnostics && hs.diagnostics.failed > 0 && (
                <span className="text-amber-300">{t('diagnosis', 'تشخیص')}: {hs.diagnostics.worst_category} — {hs.diagnostics.top_hint}</span>
              )}
            </div>
          </div>
        )}
      </div>

      {/* ── Audit findings ────────────────────────────────────────────────── */}
      {(audit?.pages ?? 0) > 0 && (
        <div className="bg-slate-800/50 border border-slate-700 rounded-lg p-4 space-y-2">
          <div className="flex flex-wrap items-center gap-3">
            <span className="font-semibold text-sm">{t('Audit-scan findings', 'یافته‌های پویش ممیزی')}</span>
            <span className="text-[11px] text-slate-400 font-mono">
              {t('pages', 'صفحات')}: {fa(audit!.pages)} · {t('flagged', 'پرچم‌خورده')}: {fa(audit!.flagged)} · {t('failed', 'ناموفق')}: {fa(audit!.failed)}
            </span>
          </div>
          <button onClick={async () => {
            setBusy(true); setMsg(null)
            try {
              const { data } = await pricingApi.nfiBackfillPageIds()
              setMsg({ kind: 'ok', text: t(
                `Page id stamped on ${fa(data.updated)} catalog items `
                + `(${fa(data.mapped)} mapped, ${fa(data.missing)} with no matching item).`,
                `شناسهٔ صفحه روی ${fa(data.updated)} قلم کاتالوگ ثبت شد `
                + `(${fa(data.mapped)} نگاشت، ${fa(data.missing)} بدون قلم متناظر).`) })
            } catch (e: unknown) {
              setMsg({ kind: 'err', text: apiErrorText(e, t('Could not stamp the page ids.', 'ثبت شناسهٔ صفحه ناموفق بود.')) })
            } finally { setBusy(false) }
          }} disabled={busy}
            title={t('Links every catalog item to its own source page — without it no suspicious row can be traced',
                     'هر قلم کاتالوگ را به صفحهٔ مبدأ خودش وصل می‌کند — بدون آن هیچ ردیف مشکوکی قابل ردیابی نیست')}
            className="px-3 py-1 text-[12px] rounded bg-indigo-700 hover:bg-indigo-600 disabled:opacity-50">
            {t('Stamp page ids onto catalog items', 'ثبت شناسهٔ صفحه روی اقلام کاتالوگ')}
          </button>
          <p className="text-[11px] text-slate-500">
            {t('The raw source of flagged pages is kept in', 'منبع خام صفحات پرچم‌خورده در')}{' '}
            <code className="font-mono">{audit!.index.replace('index.jsonl', 'pages/')}</code>{' '}
            {t('— a section missing from the source means the data was never published, not that we failed to read it.',
               'ذخیره شده است — نبودِ یک بخش در منبع یعنی داده اصلاً منتشر نشده، نه اینکه ما نخوانده‌ایم.')}
          </p>
          <div className="flex flex-wrap gap-2">
            {Object.entries(audit!.by_flag).map(([flag, count]) => (
              <span key={flag} className="text-[11px] px-2 py-0.5 rounded-full bg-slate-900 border border-slate-700 text-slate-300">
                {FLAG_LABELS[flag] ? tp(FLAG_LABELS[flag]) : flag}: <span className="font-mono">{fa(count)}</span>
              </span>))}
          </div>
        </div>
      )}

      {/* ── File import ───────────────────────────────────────────────────── */}
      <div className="bg-slate-800/50 border border-slate-700 rounded-lg p-4 space-y-2">
        <p className="font-semibold text-sm">{t('Upload the official list (Excel / CSV)', 'بارگذاری فهرست رسمی (Excel / CSV)')}</p>
        <p className="text-[11px] text-slate-500">{t('If you have the FDA\u2019s official export, upload it directly — Persian/English columns are mapped automatically.',
                                                     'اگر خروجی رسمی سازمان غذا و دارو را دارید، مستقیماً بارگذاری کنید — ستون‌های فارسی/انگلیسی خودکار نگاشت می‌شوند.')}</p>
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
