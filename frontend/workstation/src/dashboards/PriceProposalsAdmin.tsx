/**
 * PriceProposalsAdmin — manager approval of daily price-sync proposals.
 * Prices never move automatically: the sync proposes, the manager approves.
 */
import { useRef, useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { pricingApi } from '../lib/api'

interface Proposal {
  id: string; irc: string; name_fa: string; kind: 'new' | 'increase' | 'decrease'
  status: string; source: string | null
  current_effective: number; proposed_effective: number
  delta: number; pct_change: number
}

const rial = (n: number) => new Intl.NumberFormat('fa-IR').format(Math.round(n))
const KIND = {
  new:      { label: 'جدید',   cls: 'bg-blue-500/15 text-blue-300 ring-blue-500/30' },
  increase: { label: 'افزایش', cls: 'bg-red-500/15 text-red-300 ring-red-500/30' },
  decrease: { label: 'کاهش',   cls: 'bg-emerald-500/15 text-emerald-300 ring-emerald-500/30' },
}

export default function PriceProposalsAdmin() {
  const qc = useQueryClient()
  const fileRef = useRef<HTMLInputElement>(null)
  const [sel, setSel] = useState<Set<string>>(new Set())
  const [msg, setMsg] = useState<{ kind: 'ok' | 'err'; text: string } | null>(null)
  const [busy, setBusy] = useState(false)

  const importCatalog = async (file: File) => {
    setBusy(true); setMsg(null)
    try {
      const { data: res } = await pricingApi.importCatalog(file)
      setMsg({ kind: 'ok', text: `${res.imported} قلم از فهرست رسمی وارد کاتالوگ شد.` })
      qc.invalidateQueries({ queryKey: ['price-proposals'] })
    } catch (e: unknown) {
      const detail = (e as { response?: { data?: { detail?: string } } })?.response?.data?.detail
      setMsg({ kind: 'err', text: detail || 'بارگذاری فهرست ناموفق بود.' })
    } finally { setBusy(false); if (fileRef.current) fileRef.current.value = '' }
  }

  const { data } = useQuery<{ count: number; proposals: Proposal[] }>({
    queryKey: ['price-proposals'],
    queryFn: () => pricingApi.listProposals('pending').then(r => r.data),
    refetchInterval: 30_000,
  })
  const proposals = data?.proposals ?? []
  const allSelected = proposals.length > 0 && sel.size === proposals.length

  const toggle = (id: string) => setSel(p => { const n = new Set(p); n.has(id) ? n.delete(id) : n.add(id); return n })
  const toggleAll = () => setSel(allSelected ? new Set() : new Set(proposals.map(p => p.id)))

  const decide = async (approve: boolean) => {
    if (sel.size === 0) return
    setBusy(true); setMsg(null)
    try {
      const { data: res } = await pricingApi.decideProposals([...sel], approve)
      setMsg({ kind: 'ok', text: `${res.decided} مورد ${approve ? 'تأیید و اعمال شد' : 'رد شد'}.` })
      setSel(new Set()); qc.invalidateQueries({ queryKey: ['price-proposals'] })
    } catch (e: unknown) {
      const detail = (e as { response?: { data?: { detail?: string } } })?.response?.data?.detail
      setMsg({ kind: 'err', text: detail || 'عملیات ناموفق بود.' })
    } finally { setBusy(false) }
  }

  const runSync = async () => {
    setBusy(true); setMsg(null)
    try {
      const { data: res } = await pricingApi.runSync()
      setMsg({ kind: 'ok', text: res.proposals_created != null
        ? `${res.proposals_created} پیشنهاد از ${res.feed_rows} ردیف فید ساخته شد.${res.note ? ' ' + res.note : ''}`
        : (res.note || 'اجرا شد.') })
      qc.invalidateQueries({ queryKey: ['price-proposals'] })
    } catch (e: unknown) {
      const detail = (e as { response?: { data?: { detail?: string } } })?.response?.data?.detail
      setMsg({ kind: 'err', text: detail || 'اجرای همگام‌سازی ناموفق بود.' })
    } finally { setBusy(false) }
  }

  return (
    <div className="p-4 space-y-4 text-slate-100" dir="rtl">
      <div className="flex items-center gap-3">
        <h2 className="text-lg font-bold">قیمت‌زنی — تأیید تغییرات قیمت</h2>
        <span className="text-xs text-slate-500">پیشنهادهای همگام‌سازی روزانه؛ قیمت‌ها تا تأیید مدیر تغییر نمی‌کنند</span>
        <input ref={fileRef} type="file" accept=".xlsx,.xls,.csv,.tsv" className="hidden"
          onChange={e => { const f = e.target.files?.[0]; if (f) importCatalog(f) }} />
        <button onClick={() => fileRef.current?.click()} disabled={busy}
          className="ms-auto px-3 py-1.5 bg-indigo-700 hover:bg-indigo-600 text-sm rounded-lg disabled:opacity-50">
          ⬆ بارگذاری فهرست رسمی (Excel/CSV)
        </button>
        <button onClick={runSync} disabled={busy}
          className="px-3 py-1.5 bg-slate-700 hover:bg-slate-600 text-sm rounded-lg disabled:opacity-50">
          ⟳ اجرای همگام‌سازی
        </button>
      </div>

      {msg && <p className={`text-sm ${msg.kind === 'ok' ? 'text-emerald-400' : 'text-red-400'}`}>{msg.text}</p>}

      <div className="flex items-center gap-2">
        <button onClick={() => decide(true)} disabled={busy || sel.size === 0}
          className="px-4 py-2 bg-emerald-600 hover:bg-emerald-500 text-sm rounded-lg disabled:opacity-40">
          تأیید و اعمال ({sel.size})
        </button>
        <button onClick={() => decide(false)} disabled={busy || sel.size === 0}
          className="px-4 py-2 bg-slate-700 hover:bg-slate-600 text-sm rounded-lg disabled:opacity-40">
          رد
        </button>
        <span className="text-xs text-slate-500 ms-auto">{proposals.length} پیشنهاد در انتظار</span>
      </div>

      <div className="bg-slate-800/50 border border-slate-700 rounded-lg overflow-hidden">
        <table className="w-full text-sm">
          <thead className="bg-slate-800 text-slate-400 text-xs">
            <tr>
              <th className="p-2 w-8"><input type="checkbox" checked={allSelected} onChange={toggleAll} /></th>
              <th className="p-2 text-right">دارو</th>
              <th className="p-2">نوع</th>
              <th className="p-2 text-right">قیمت فعلی</th>
              <th className="p-2 text-right">قیمت پیشنهادی</th>
              <th className="p-2 text-right">تغییر</th>
              <th className="p-2">منبع</th>
            </tr>
          </thead>
          <tbody>
            {proposals.length === 0 && (
              <tr><td colSpan={7} className="p-6 text-center text-slate-500">پیشنهاد در انتظاری نیست — همگام‌سازی را اجرا کنید.</td></tr>
            )}
            {proposals.map(p => {
              const k = KIND[p.kind]
              return (
                <tr key={p.id} className="border-t border-slate-700/60 hover:bg-white/[0.02]">
                  <td className="p-2 text-center"><input type="checkbox" checked={sel.has(p.id)} onChange={() => toggle(p.id)} /></td>
                  <td className="p-2 text-right">{p.name_fa}<div className="text-[10px] text-slate-500 font-mono">{p.irc}</div></td>
                  <td className="p-2 text-center"><span className={`text-[10px] px-2 py-0.5 rounded-full ring-1 ring-inset ${k.cls}`}>{k.label}</span></td>
                  <td className="p-2 text-right font-mono tabular-nums text-slate-400">{rial(p.current_effective)}</td>
                  <td className="p-2 text-right font-mono tabular-nums font-semibold">{rial(p.proposed_effective)}</td>
                  <td className={`p-2 text-right font-mono tabular-nums ${p.delta >= 0 ? 'text-red-300' : 'text-emerald-300'}`}>
                    {p.delta >= 0 ? '+' : ''}{rial(p.delta)} <span className="text-[10px]">({p.pct_change >= 0 ? '+' : ''}{p.pct_change.toFixed(0)}٪)</span>
                  </td>
                  <td className="p-2 text-center text-[10px] text-slate-500">{p.source || '—'}</td>
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>
      <p className="text-[11px] text-slate-600">
        قیمت مؤثر = بیشینهٔ قیمت اعلام‌شده (NFI) و آخرین فاکتور خرید. تأیید، قیمت کاتالوگ را به‌روزرسانی می‌کند و در لاگ ثبت می‌شود.
      </p>
    </div>
  )
}
