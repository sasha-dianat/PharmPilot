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
  quarantined?: number
  last_id: number; start_id: number; end_id: number; progress_pct: number
  elapsed_sec: number; eta_sec: number | null; message: string; error: string | null
  diagnostics?: { failed: number; worst_category: string | null; top_hint: string | null }
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
      await pricingApi.nfiStart({ start_id: startId, end_id: endId, delay, proxy: proxy || undefined })
      qc.invalidateQueries({ queryKey: ['nfi-status'] })
    } catch (e: unknown) {
      setMsg({ kind: 'err', text: apiErrorText(e, 'شروع برداشت ناموفق بود.') })
    } finally { setBusy(false) }
  }
  const stop = async () => { await pricingApi.nfiStop(); qc.invalidateQueries({ queryKey: ['nfi-status'] }) }

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
            : <button onClick={start} disabled={busy} className="px-4 py-1.5 bg-emerald-600 hover:bg-emerald-500 rounded-lg disabled:opacity-50">شروع برداشت</button>}
        </div>

        {hs && (hs.running || hs.scanned > 0) && (
          <div className="space-y-1.5">
            <div className="h-2 bg-slate-700 rounded-full overflow-hidden">
              <div className="h-full bg-emerald-500 transition-all" style={{ width: `${Math.min(100, hs.progress_pct)}%` }} />
            </div>
            <div className="flex flex-wrap gap-x-5 gap-y-1 text-[11px] text-slate-400 font-mono">
              <span>{hs.progress_pct}٪</span>
              <span>پیموده‌شده: {fa(hs.scanned)}</span>
              <span className="text-emerald-300">محصولات: {fa(hs.products)}</span>
              <span className="text-indigo-300">ثبت‌شده: {fa(hs.ingested)}</span>
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
