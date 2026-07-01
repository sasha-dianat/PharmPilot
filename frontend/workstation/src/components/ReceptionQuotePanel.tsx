/**
 * ReceptionQuotePanel — the receptionist's affordability-negotiation surface.
 * ==========================================================================
 * On intake the client is quoted the basket total (covered + non-covered). If
 * unaffordable, the receptionist applies levers IN PRIORITY ORDER, each lever
 * re-quoting live against the Iranian pricing engine + drug catalog:
 *   1. swap to a cheaper brand/generic (same active ingredient)
 *   2. remove non-essential supplements
 *   3. reduce item counts
 * When the patient agrees → send to filling.
 *
 * Guided suggestions, applied manually (per the agreed UX). Backed by
 * POST /pricing/quote. Money is Rial.
 */
import { useMemo, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { pricingApi, type QuoteLine } from '../lib/api'

interface BasketItem { drug_name: string; quantity: number }
interface AltOut {
  irc: string; name: string; brand_name: string | null; is_generic: boolean
  unit_price: number; savings_per_unit: number; savings_total: number
}
interface LineOut {
  irc?: string; name?: string; generic_name?: string; brand_name?: string | null
  is_generic?: boolean; category?: string; quantity?: number; unit_price?: number
  gross?: number; covered?: boolean; insurer_share?: number; patient_share?: number
  differential?: number; vat?: number; patient_total?: number
  alternatives?: AltOut[]; removed?: boolean; unmatched?: boolean; drug_name?: string
}
interface QuoteOut {
  insurer: string; insurer_name_fa: string; setting: string; lines: LineOut[]
  technical_fee: { total: number; insurer: number; patient: number }
  totals: { gross: number; insurer: number; patient: number; differential: number; vat: number; grand_total: number }
  notes: string[]
}

const INSURERS = [
  { code: 'tamin', label: 'تأمین اجتماعی' },
  { code: 'salamat', label: 'بیمه سلامت' },
  { code: 'armed_forces', label: 'نیروهای مسلح' },
  { code: 'cash', label: 'آزاد (بدون بیمه)' },
]
const rial = (n: number | undefined) => `${new Intl.NumberFormat('fa-IR').format(Math.round(n ?? 0))} ﷼`

// editable line state — irc overrides the name once an alternative is chosen
interface EditLine { drug_name: string; irc: string | null; quantity: number; removed: boolean }

export default function ReceptionQuotePanel({
  patientName, items, onClose, onSendToFilling,
}: {
  patientName: string
  items: BasketItem[]
  onClose: () => void
  onSendToFilling?: (lines: QuoteLine[]) => void
}) {
  const [insurer, setInsurer] = useState('tamin')
  const [setting, setSetting] = useState('outpatient')
  const [techFee, setTechFee] = useState(200000)
  const [target, setTarget] = useState<number | ''>('')
  const [lines, setLines] = useState<EditLine[]>(
    () => items.map(i => ({ drug_name: i.drug_name, irc: null, quantity: i.quantity || 1, removed: false }))
  )

  const payloadLines: QuoteLine[] = lines.map(l => ({
    irc: l.irc, drug_name: l.irc ? null : l.drug_name, quantity: l.quantity, removed: l.removed,
  }))

  const { data, isFetching } = useQuery<QuoteOut>({
    queryKey: ['repricing', insurer, setting, techFee, JSON.stringify(payloadLines)],
    queryFn: () => pricingApi.quote({ insurer, setting, technical_fee: techFee, lines: payloadLines }).then(r => r.data),
  })

  const patientPays = data?.totals.patient ?? 0
  const overBudget = target !== '' && patientPays > Number(target)

  const set = (i: number, patch: Partial<EditLine>) =>
    setLines(prev => prev.map((l, k) => (k === i ? { ...l, ...patch } : l)))

  // map quote line (by index, since order preserved) for display
  const quoteByIndex = useMemo(() => data?.lines ?? [], [data])

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4" onClick={onClose}>
      <div className="cd-scope bg-canvas w-full max-w-3xl max-h-[90vh] rounded-2xl overflow-hidden flex flex-col shadow-2xl"
        onClick={e => e.stopPropagation()}>

        {/* ── Header ─────────────────────────────────────────────────────── */}
        <div className="px-5 py-3.5 bg-surface border-b border-line flex items-center gap-3">
          <span className="w-7 h-7 rounded-lg bg-intel-soft text-intel flex items-center justify-center">💳</span>
          <div className="min-w-0">
            <h2 className="cd-ui text-[15px] font-semibold text-ink">Reception quote · {patientName}</h2>
            <p className="cd-ui text-[11px] text-ink3">قیمت‌گذاری نسخه و تطبیق با توان پرداخت بیمار</p>
          </div>
          <button onClick={onClose} className="cd-ui ml-auto text-ink3 hover:text-ink text-sm px-2">✕</button>
        </div>

        {/* ── Controls ───────────────────────────────────────────────────── */}
        <div className="px-5 py-2.5 bg-surface2 border-b border-line flex flex-wrap items-center gap-3 text-[12px]">
          <label className="flex items-center gap-1.5">
            <span className="text-ink3">بیمه</span>
            <select value={insurer} onChange={e => setInsurer(e.target.value)}
              className="cd-ui bg-surface border border-line2 rounded-lg px-2 py-1 text-ink">
              {INSURERS.map(o => <option key={o.code} value={o.code}>{o.label}</option>)}
            </select>
          </label>
          <label className="flex items-center gap-1.5">
            <span className="text-ink3">نوع</span>
            <select value={setting} onChange={e => setSetting(e.target.value)}
              className="cd-ui bg-surface border border-line2 rounded-lg px-2 py-1 text-ink">
              <option value="outpatient">سرپایی</option>
              <option value="inpatient">بستری</option>
            </select>
          </label>
          <label className="flex items-center gap-1.5">
            <span className="text-ink3">حق فنی</span>
            <input type="number" value={techFee} onChange={e => setTechFee(Number(e.target.value) || 0)}
              className="cd-data w-24 bg-surface border border-line2 rounded-lg px-2 py-1 text-ink" />
          </label>
          <label className="flex items-center gap-1.5 ml-auto">
            <span className="text-ink3">توان پرداخت بیمار</span>
            <input type="number" placeholder="—" value={target}
              onChange={e => setTarget(e.target.value === '' ? '' : Number(e.target.value))}
              className="cd-data w-28 bg-surface border border-line2 rounded-lg px-2 py-1 text-ink" />
          </label>
          {isFetching && <span className="cd-ui text-[11px] text-intel">…محاسبه</span>}
        </div>

        {/* ── Lines ──────────────────────────────────────────────────────── */}
        <div className="flex-1 overflow-y-auto px-4 py-3 space-y-2">
          {lines.map((l, i) => {
            const q = quoteByIndex[i]
            const isSupp = q?.category === 'supplement' || q?.category === 'cosmetic'
            return (
              <div key={i} className={`cd-card p-3 ${l.removed ? 'opacity-50' : ''}`}>
                <div className="flex items-start gap-2">
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-1.5 flex-wrap">
                      <span className="cd-ui text-[13px] font-bold text-ink">{q?.name || l.drug_name}</span>
                      {q?.brand_name && <span className="cd-ui text-[10px] bg-counsel-soft text-counsel border border-counsel/30 rounded px-1">برند</span>}
                      {q?.is_generic && <span className="cd-ui text-[10px] bg-safe-soft text-safe border border-safe/30 rounded px-1">ژنریک</span>}
                      {isSupp && <span className="cd-ui text-[10px] bg-warning-soft text-warning border border-warning/30 rounded px-1">مکمل/آرایشی — بدون پوشش</span>}
                      {q?.unmatched && <span className="cd-ui text-[10px] bg-blocker-soft text-blocker border border-blocker/30 rounded px-1">در فهرست یافت نشد</span>}
                    </div>
                    {!q?.unmatched && !l.removed && (
                      <div className="cd-data text-[11px] text-ink2 mt-1 flex flex-wrap gap-x-3 gap-y-0.5">
                        <span>قیمت واحد: <b>{rial(q?.unit_price)}</b></span>
                        <span>کل: <b>{rial(q?.gross)}</b></span>
                        <span className="text-safe">سهم بیمه: {rial(q?.insurer_share)}</span>
                        <span className="text-intel">سهم بیمار: <b>{rial(q?.patient_total)}</b></span>
                        {(q?.differential ?? 0) > 0 && <span className="text-caution">مابه‌التفاوت: {rial(q?.differential)}</span>}
                      </div>
                    )}
                  </div>
                  {/* qty stepper (lever 3) */}
                  {!l.removed && !q?.unmatched && (
                    <div className="flex items-center gap-1 flex-shrink-0">
                      <button onClick={() => set(i, { quantity: Math.max(1, l.quantity - 1) })}
                        className="cd-ui w-6 h-6 rounded border border-line2 text-ink2 hover:bg-surface2">−</button>
                      <span className="cd-data text-[12px] w-8 text-center">{l.quantity}</span>
                      <button onClick={() => set(i, { quantity: l.quantity + 1 })}
                        className="cd-ui w-6 h-6 rounded border border-line2 text-ink2 hover:bg-surface2">+</button>
                    </div>
                  )}
                  {/* remove (lever 2) */}
                  <button onClick={() => set(i, { removed: !l.removed })}
                    className={`cd-ui text-[11px] px-2 py-1 rounded border flex-shrink-0 ${
                      l.removed ? 'border-safe/40 text-safe' : 'border-line2 text-ink3 hover:bg-surface2'}`}>
                    {l.removed ? 'افزودن' : 'حذف'}
                  </button>
                </div>

                {/* cheaper alternatives (lever 1) */}
                {!l.removed && (q?.alternatives?.length ?? 0) > 0 && (
                  <div className="mt-2 pt-2 border-t border-line">
                    <div className="cd-ui text-[10px] text-ink3 mb-1">جایگزین ارزان‌تر (همان ماده مؤثره):</div>
                    <div className="flex flex-wrap gap-1.5">
                      {q!.alternatives!.map(a => (
                        <button key={a.irc} onClick={() => set(i, { irc: a.irc })}
                          className="cd-ui text-[11px] px-2 py-1 rounded-lg border border-intel/30 bg-intel-soft text-intel hover:brightness-105 text-right">
                          {a.brand_name || a.name} · {rial(a.unit_price)}
                          <span className="text-safe block text-[10px]">صرفه‌جویی {rial(a.savings_total)}</span>
                        </button>
                      ))}
                      {l.irc && (
                        <button onClick={() => set(i, { irc: null })}
                          className="cd-ui text-[11px] px-2 py-1 rounded-lg border border-line2 text-ink3 hover:bg-surface2">بازگردانی به نسخه</button>
                      )}
                    </div>
                  </div>
                )}
              </div>
            )
          })}
        </div>

        {/* ── Totals + actions ───────────────────────────────────────────── */}
        <div className="border-t border-line bg-surface px-5 py-3 space-y-2">
          {data?.notes?.map((n, i) => <p key={i} className="cd-ui text-[11px] text-ink3 italic">• {n}</p>)}
          <div className="flex items-end gap-5">
            <Tot label="کل نسخه" v={data?.totals.gross} />
            <Tot label="سهم بیمه" v={data?.totals.insurer} tone="safe" />
            <Tot label="پرداختی بیمار" v={patientPays} tone={overBudget ? 'blocker' : 'intel'} big />
            {(data?.totals.differential ?? 0) > 0 && <Tot label="مابه‌التفاوت" v={data?.totals.differential} tone="caution" />}
            <div className="ml-auto flex gap-2">
              <button onClick={onClose} className="cd-ui px-3 py-2 text-sm rounded-lg border border-line2 text-ink2 hover:bg-surface2">انصراف</button>
              <button onClick={() => onSendToFilling?.(payloadLines)}
                disabled={overBudget}
                title={overBudget ? 'هنوز بالاتر از توان پرداخت بیمار است' : ''}
                className="cd-ui px-4 py-2 text-sm rounded-lg bg-safe text-white hover:brightness-110 disabled:opacity-40">
                تأیید و ارسال به نسخه‌پیچی →
              </button>
            </div>
          </div>
          {overBudget && (
            <p className="cd-ui text-[12px] text-blocker">
              {rial(patientPays - Number(target))} بالاتر از توان پرداخت — جایگزین ارزان‌تر، حذف مکمل یا کاهش تعداد را اعمال کنید.
            </p>
          )}
        </div>
      </div>
    </div>
  )
}

function Tot({ label, v, tone = 'ink', big = false }: { label: string; v?: number; tone?: string; big?: boolean }) {
  const color = tone === 'safe' ? 'text-safe' : tone === 'intel' ? 'text-intel'
    : tone === 'blocker' ? 'text-blocker' : tone === 'caution' ? 'text-caution' : 'text-ink'
  return (
    <div className="flex flex-col">
      <span className="cd-ui text-[10px] text-ink3">{label}</span>
      <span className={`cd-data ${big ? 'text-[18px] font-bold' : 'text-[13px] font-semibold'} ${color}`}>
        {new Intl.NumberFormat('fa-IR').format(Math.round(v ?? 0))} ﷼
      </span>
    </div>
  )
}
