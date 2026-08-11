/**
 * InventoryAdmin — «مدیریت موجودی»
 *
 * The administrator's working surface: one search box over every identifier,
 * named filter views with live counts, a sortable item table, and a detail
 * drawer holding the lots in FEFO order plus the complete movement history.
 *
 * It can correct anything except a quantity. Location, cost, par levels and the
 * formulary binding edit inline; extending an expiry or releasing blocked stock
 * goes to the approval queue instead of applying. Stock only ever moves through
 * receiving, dispensing, a counted variance, or an approved write-off — so the
 * ledger keeps explaining every unit. That restriction is the reason this much
 * power is safe, and the UI says so rather than hiding it.
 */
import { useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { inventoryAdminApi, apiErrorText } from '../lib/api'
import { useLang } from '../lib/i18n'
import { FILTER_LABEL_EN, SORT_LABEL, serverLabel } from '../lib/serverLabels'

interface Item {
  ndc11: string; irc: string | null; drug_name: string
  generic_name: string | null; strength: string | null; dosage_form: string | null
  is_controlled: boolean; requires_refrigeration: boolean
  on_hand: number; quantity_reserved: number; quantity_on_order: number
  par_level_min: number | null; par_level_max: number | null
  avg_daily_demand: number | null; days_supply: number | null
  value: number; next_expiry: string | null; expired: boolean
  lot_count: number; quarantined_lots: number; recalled_lots: number
  breached_lots: number; last_dispensed_at: string | null
  announced_price: number | null; package_count: number | null
}
interface Lot {
  id: string; lot_number: string; irc: string | null; expiry_date: string | null
  quantity_on_hand: number; quantity_reserved: number; unit_cost: number | null
  storage_location: string | null; days_to_expiry: number | null
  is_quarantined: boolean; is_recalled: boolean; cold_chain_breach: boolean
  blocked_reason: string | null; serial_number: string | null
}
interface Movement {
  id: string; movement_type: string; reason: string; delta: number
  before: number; after: number; at: string | null; actor: string | null
  actor_role: string | null
  chained: boolean; fill_id: string | null; approval_id: string | null
}
interface Detail {
  item: Record<string, unknown>; lots: Lot[]; movements: Movement[]
  editable_fields: { lot_direct: string[]; lot_sensitive: string[]
                     ledger_only: string[]; stock_direct: string[] }
}

type Pair = readonly [string, string]

const BLOCKED: Record<string, Pair> = {
  recalled: ['Recalled', 'فراخوان‌شده'], cold_chain_breach: ['Cold chain broken', 'زنجیره سرد شکسته'],
  quarantined: ['Quarantined', 'قرنطینه'], expired: ['Expired', 'منقضی'],
}
const MV: Record<string, Pair> = {
  RECEIPT: ['Receipt', 'دریافت'], DISPENSE: ['Dispense', 'تحویل'], WASTE: ['Waste', 'ضایعات'],
  EXPIRY_REMOVAL: ['Expiry removal', 'حذف انقضا'], RECALL_REMOVAL: ['Recall removal', 'حذف فراخوان'],
  RETURN_TO_SUPPLIER: ['Return to supplier', 'مرجوعی'], COUNT_GAIN: ['Count gain', 'اضافه شمارش'],
  COUNT_LOSS: ['Count loss', 'کسری شمارش'], ADJUSTMENT: ['Adjustment', 'اصلاح'],
  CORRECTION: ['Correction', 'تصحیح'],
  TRANSFER_IN: ['Transfer in', 'انتقال ورودی'], TRANSFER_OUT: ['Transfer out', 'انتقال خروجی'],
}
const FIELD: Record<string, Pair> = {
  storage_location: ['Storage location', 'محل نگهداری'], unit_cost: ['Unit cost', 'قیمت واحد'],
  irc: ['IRC code', 'کد IRC'], lot_number: ['Lot number', 'شماره بچ'],
  serial_number: ['Serial number', 'شماره سریال'], expiry_date: ['Expiry date', 'تاریخ انقضا'],
  is_quarantined: ['Quarantined', 'قرنطینه'], is_recalled: ['Recalled', 'فراخوان'],
  cold_chain_breach: ['Cold chain', 'زنجیره سرد'],
  par_level_min: ['Minimum stock', 'حداقل موجودی'], par_level_max: ['Maximum stock', 'حداکثر موجودی'],
}

export default function InventoryAdmin() {
  const { t, tp, n: fa, dir, lang, date, dateTime } = useLang()
  // numeric Jalali in table cells — the long form ("۱۵ خرداد ۱۴۰۳") wraps the column
  const faDate = (s: string | null) => date(s, { short: true })
  const fieldName = (f: string) => (FIELD[f] ? tp(FIELD[f]) : f)
  const qc = useQueryClient()
  const [q, setQ] = useState('')
  const [filter, setFilter] = useState('all')
  const [sort, setSort] = useState('name')
  const [page, setPage] = useState(0)
  const [openNdc, setOpenNdc] = useState<string | null>(null)
  const [selected, setSelected] = useState<Set<string>>(new Set())
  const [msg, setMsg] = useState<{ kind: 'ok' | 'err' | 'warn'; text: string } | null>(null)
  const [showReceive, setShowReceive] = useState(false)
  const LIMIT = 25

  const { data: chips } = useQuery<{ counts: Record<string, number>
                                     labels: Record<string, string>; sorts: string[] }>({
    queryKey: ['inv-admin-filters'],
    queryFn: () => inventoryAdminApi.filters().then(r => r.data),
    refetchInterval: 60_000,
  })
  const { data, isLoading, error } = useQuery<{ items: Item[]; total: number }>({
    queryKey: ['inv-admin-items', q, filter, sort, page],
    queryFn: () => inventoryAdminApi.items({
      q: q || undefined, filter, sort, limit: LIMIT, offset: page * LIMIT,
    }).then(r => r.data),
  })
  const { data: detail } = useQuery<Detail>({
    queryKey: ['inv-admin-detail', openNdc],
    queryFn: () => inventoryAdminApi.detail(openNdc!).then(r => r.data),
    enabled: !!openNdc,
  })

  const refresh = () => {
    qc.invalidateQueries({ queryKey: ['inv-admin-items'] })
    qc.invalidateQueries({ queryKey: ['inv-admin-detail'] })
    qc.invalidateQueries({ queryKey: ['inv-admin-filters'] })
    qc.invalidateQueries({ queryKey: ['inv-approvals'] })
  }

  const editLot = async (lotId: string, field: string, current: unknown) => {
    const raw = window.prompt(t(`New value for “${fieldName(field)}”`, `مقدار جدید برای «${fieldName(field)}»`),
                              current == null ? '' : String(current))
    if (raw === null) return
    const reason = window.prompt(t('Reason for this correction? (it is written to the record)',
                                   'دلیل این اصلاح؟ (در سابقه ثبت می‌شود)'))
    if (!reason) return
    let value: unknown = raw
    if (field === 'unit_cost') value = Number(raw)
    if (field === 'is_quarantined' || field === 'is_recalled' || field === 'cold_chain_breach')
      value = raw === 'true' || raw === '1' || raw === 'بله' || raw === 'yes'
    try {
      const { data } = await inventoryAdminApi.editLot(lotId, { field, value, reason })
      setMsg(data.applied
        ? { kind: 'ok', text: t(`“${fieldName(field)}” corrected.`, `«${fieldName(field)}» اصلاح شد.`) }
        : { kind: 'warn', text: data.message })
      refresh()
    } catch (e) { setMsg({ kind: 'err', text: apiErrorText(e) }) }
  }

  const bulkEdit = async (field: string) => {
    const ids = [...selected]
    if (!ids.length) return
    const raw = window.prompt(t(`“${fieldName(field)}” for ${fa(ids.length)} selected lots`,
                                `«${fieldName(field)}» برای ${fa(ids.length)} بچ انتخاب‌شده`))
    if (raw === null) return
    const reason = window.prompt(t('Reason?', 'دلیل؟'))
    if (!reason) return
    const value: unknown = field === 'is_quarantined' ? true : raw
    try {
      const { data } = await inventoryAdminApi.bulkEdit({ lot_ids: ids, field, value, reason })
      setMsg({ kind: 'ok', text: t(`${fa(data.changed)} lots changed, ${fa(data.unchanged)} unchanged.`,
                                   `${fa(data.changed)} بچ تغییر کرد، ${fa(data.unchanged)} بدون تغییر.`) })
      setSelected(new Set()); refresh()
    } catch (e) { setMsg({ kind: 'err', text: apiErrorText(e) }) }
  }

  const writeOff = async (lot: Lot) => {
    const type = window.prompt(
      t('Write-off type: WASTE / EXPIRY_REMOVAL / RECALL_REMOVAL / RETURN_TO_SUPPLIER',
        'نوع کسر: WASTE / EXPIRY_REMOVAL / RECALL_REMOVAL / RETURN_TO_SUPPLIER'),
      lot.blocked_reason === 'expired' ? 'EXPIRY_REMOVAL' : 'WASTE')
    if (!type) return
    const qty = Number(window.prompt(t(`Quantity (on hand: ${lot.quantity_on_hand})`,
                                       `تعداد (موجودی: ${lot.quantity_on_hand})`),
                                     String(lot.quantity_on_hand)))
    if (!qty) return
    const reason = window.prompt(t('Reason?', 'دلیل؟'))
    if (!reason) return
    try {
      const { data } = await inventoryAdminApi.writeOff({
        lot_id: lot.id, movement_type: type, quantity: qty, reason })
      setMsg({ kind: 'warn', text: data.message })
      refresh()
    } catch (e) { setMsg({ kind: 'err', text: apiErrorText(e) }) }
  }

  const totalPages = Math.ceil((data?.total ?? 0) / LIMIT)

  return (
    <div className="p-4 space-y-3 text-slate-100" dir={dir}>
      <div className="flex flex-wrap items-center gap-3">
        <h2 className="text-lg font-bold">{t('Inventory admin', 'مدیریت موجودی')}</h2>
        <span className="text-[11px] text-slate-500">
          {t('Quantities move only through a receipt, a dispense, a count or an approved write-off — they are not editable here.',
             'تعداد فقط از راه دریافت، تحویل، شمارش یا کسرِ تأییدشده تغییر می‌کند — در این صفحه قابل ویرایش نیست.')}
        </span>
        <button onClick={() => setShowReceive(v => !v)}
                className="ms-auto px-3 py-1.5 bg-emerald-600 hover:bg-emerald-500 rounded-lg text-sm">
          + {t('Receive goods', 'دریافت کالا')}
        </button>
      </div>
      {msg && (
        <p className={`text-sm ${msg.kind === 'ok' ? 'text-emerald-400'
          : msg.kind === 'warn' ? 'text-amber-300' : 'text-red-400'}`}>{msg.text}</p>)}
      {error && <p className="text-sm text-red-400">{apiErrorText(error)}</p>}

      {showReceive && <ReceiveForm onDone={(t) => { setMsg(t); setShowReceive(false); refresh() }} />}

      {/* ── search + sort ─────────────────────────────────────────────── */}
      <div className="flex flex-wrap gap-2 items-center">
        <input value={q} onChange={e => { setQ(e.target.value); setPage(0) }}
               placeholder={t('Drug name, IRC, NDC, barcode or lot number…', 'نام دارو، IRC، NDC، بارکد یا شماره بچ…')}
               className="flex-1 min-w-[260px] bg-slate-900 border border-slate-600 rounded px-3 py-1.5 text-sm" />
        <select value={sort} onChange={e => setSort(e.target.value)}
                className="bg-slate-900 border border-slate-600 rounded px-2 py-1.5 text-sm">
          {(chips?.sorts ?? []).map(s =>
            <option key={s} value={s}>{SORT_LABEL[s] ? tp(SORT_LABEL[s]) : s}</option>)}
        </select>
      </div>

      {/* ── filter chips with live counts ─────────────────────────────── */}
      <div className="flex flex-wrap gap-1.5">
        {Object.entries(chips?.labels ?? {}).map(([k, label]) => {
          const n = chips?.counts?.[k] ?? 0
          const active = filter === k
          const alarming = ['expired', 'recalled', 'cold_chain'].includes(k) && n > 0
          return (
            <button key={k} onClick={() => { setFilter(k); setPage(0) }}
              className={`text-[11px] px-2.5 py-1 rounded-full border transition-colors ${
                active ? 'bg-indigo-600/25 border-indigo-500 text-indigo-200'
                : alarming ? 'bg-rose-500/10 border-rose-500/50 text-rose-300 hover:border-rose-400'
                : 'bg-slate-900 border-slate-700 text-slate-400 hover:border-slate-500'}`}>
              {serverLabel(lang, FILTER_LABEL_EN, k, label)} <span className="font-mono">{fa(n)}</span>
            </button>)
        })}
      </div>

      {/* ── item table ────────────────────────────────────────────────── */}
      <div className="bg-slate-800/40 border border-slate-700 rounded-lg overflow-x-auto">
        <table className="w-full text-[12px]">
          <thead className="text-slate-500 text-[10px] border-b border-slate-700">
            <tr>
              {[t('Drug', 'دارو'), t('On hand', 'موجودی'), t('Reserved', 'رزرو'),
                t('Available', 'قابل تعهد'), t('Days of cover', 'روز پوشش'),
                t('Reorder level', 'حد سفارش'), t('Nearest expiry', 'نزدیک‌ترین انقضا'),
                t('Lots', 'بچ‌ها'), t('Value', 'ارزش'), t('Status', 'وضعیت')].map(h =>
                <th key={h} className="text-start px-3 py-2 font-normal whitespace-nowrap">{h}</th>)}
            </tr>
          </thead>
          <tbody>
            {isLoading && <tr><td colSpan={10} className="px-3 py-6 text-center text-slate-500">{t('Loading…', 'در حال بارگذاری…')}</td></tr>}
            {data?.items.length === 0 && (
              <tr><td colSpan={10} className="px-3 py-6 text-center text-slate-500">{t('Nothing found.', 'موردی یافت نشد.')}</td></tr>)}
            {data?.items.map(it => (
              <tr key={it.ndc11}
                  onClick={() => setOpenNdc(openNdc === it.ndc11 ? null : it.ndc11)}
                  className={`border-b border-slate-800 hover:bg-slate-700/30 cursor-pointer ${
                    openNdc === it.ndc11 ? 'bg-slate-700/40' : ''}`}>
                <td className="px-3 py-2">
                  <div className="font-medium">{it.drug_name}</div>
                  <div className="text-[10px] text-slate-500 font-mono">
                    {it.irc || it.ndc11}{it.strength ? ` · ${it.strength}` : ''}
                  </div>
                </td>
                <td className="px-3 py-2 font-mono tabular-nums">{fa(it.on_hand)}</td>
                <td className="px-3 py-2 font-mono tabular-nums text-slate-400">{fa(it.quantity_reserved)}</td>
                {/* Available, not on-hand, is what can be promised to the next
                    patient. Reservations were never written before, so this
                    column always equalled on-hand and told nobody anything. */}
                <td className={`px-3 py-2 font-mono tabular-nums ${
                  it.on_hand - it.quantity_reserved <= 0 && it.on_hand > 0
                    ? 'text-amber-300' : ''}`}
                    title={it.quantity_reserved > 0
                      ? t('On hand minus what is committed to un-dispensed prescriptions',
                          'موجودی منهای آنچه به نسخه‌های تحویل‌نشده تعهد شده است')
                      : undefined}>
                  {fa(it.on_hand - it.quantity_reserved)}
                </td>
                <td className={`px-3 py-2 font-mono tabular-nums ${
                  it.days_supply != null && it.days_supply < 14 ? 'text-amber-300' : ''}`}>
                  {it.days_supply != null ? fa(it.days_supply) : '—'}
                </td>
                <td className="px-3 py-2 font-mono tabular-nums text-slate-400">{fa(it.par_level_min)}</td>
                <td className={`px-3 py-2 whitespace-nowrap ${it.expired ? 'text-rose-300' : ''}`}>
                  {faDate(it.next_expiry)}
                </td>
                <td className="px-3 py-2 font-mono tabular-nums text-slate-400">{fa(it.lot_count)}</td>
                <td className="px-3 py-2 font-mono tabular-nums text-slate-400">{fa(Math.round(it.value))}</td>
                <td className="px-3 py-2">
                  <div className="flex flex-wrap gap-1">
                    {it.is_controlled && <Badge tone="rose">{t('Controlled', 'تحت کنترل')}</Badge>}
                    {it.requires_refrigeration && <Badge tone="sky">{t('Refrigerated', 'یخچالی')}</Badge>}
                    {it.expired && <Badge tone="rose">{t('Expired', 'منقضی')}</Badge>}
                    {it.recalled_lots > 0 && <Badge tone="rose">{t('Recalled', 'فراخوان')} {fa(it.recalled_lots)}</Badge>}
                    {it.quarantined_lots > 0 && <Badge tone="amber">{t('Quarantined', 'قرنطینه')} {fa(it.quarantined_lots)}</Badge>}
                    {it.breached_lots > 0 && <Badge tone="amber">{t('Cold chain', 'زنجیره سرد')}</Badge>}
                    {!it.irc && <Badge tone="slate">{t('No IRC', 'بدون IRC')}</Badge>}
                    {it.par_level_min != null && it.on_hand < it.par_level_min &&
                      <Badge tone="amber">{t('Below level', 'زیر حد')}</Badge>}
                  </div>
                </td>
              </tr>))}
          </tbody>
        </table>
      </div>

      <div className="flex items-center gap-3 text-[11px] text-slate-400">
        <span>{t(`${fa(data?.total ?? 0)} items`, `${fa(data?.total ?? 0)} قلم`)}</span>
        {totalPages > 1 && (
          <>
            <button disabled={page === 0} onClick={() => setPage(p => p - 1)}
                    className="px-2 py-0.5 bg-slate-800 rounded disabled:opacity-40">{t('Previous', 'قبلی')}</button>
            <span>{t(`Page ${fa(page + 1)} of ${fa(totalPages)}`, `صفحه ${fa(page + 1)} از ${fa(totalPages)}`)}</span>
            <button disabled={page + 1 >= totalPages} onClick={() => setPage(p => p + 1)}
                    className="px-2 py-0.5 bg-slate-800 rounded disabled:opacity-40">{t('Next', 'بعدی')}</button>
          </>)}
        {selected.size > 0 && (
          <span className="ms-auto flex items-center gap-2">
            <span className="text-indigo-300">{t(`${fa(selected.size)} lots selected`, `${fa(selected.size)} بچ انتخاب شده`)}</span>
            <button onClick={() => bulkEdit('storage_location')}
                    className="px-2 py-0.5 bg-indigo-600 hover:bg-indigo-500 rounded">{t('Bulk relocate', 'جابجایی گروهی')}</button>
            <button onClick={() => bulkEdit('is_quarantined')}
                    className="px-2 py-0.5 bg-amber-700 hover:bg-amber-600 rounded">{t('Bulk quarantine', 'قرنطینه گروهی')}</button>
            <button onClick={() => setSelected(new Set())}
                    className="px-2 py-0.5 bg-slate-800 rounded">{t('Cancel', 'لغو')}</button>
          </span>)}
      </div>

      {/* ── detail drawer ─────────────────────────────────────────────── */}
      {openNdc && detail && (
        <div className="bg-slate-800/60 border border-slate-600 rounded-lg p-4 space-y-4">
          <div className="flex flex-wrap items-center gap-3">
            <span className="font-semibold">{String(detail.item.name_fa || detail.item.generic_name || openNdc)}</span>
            <span className="text-[11px] font-mono text-slate-500">
              IRC {String(detail.item.irc ?? '—')} · NDC {openNdc}
              {detail.item.manufacturer ? ` · ${detail.item.manufacturer}` : ''}
            </span>
            <button onClick={() => setOpenNdc(null)} className="ms-auto text-slate-400 text-sm">{t('Close', 'بستن')} ✕</button>
          </div>

          {/* lots */}
          <div>
            <p className="text-[12px] font-semibold mb-1">{t('Lots — in issue order (nearest expiry first)',
                                                                'بچ‌ها — به ترتیب مصرف (نزدیک‌ترین انقضا اول)')}</p>
            <div className="overflow-x-auto">
              <table className="w-full text-[11px]">
                <thead className="text-slate-500 text-[10px]">
                  <tr>
                    <th className="px-2 py-1"></th>
                    {[t('Lot', 'بچ'), t('Expiry', 'انقضا'), t('Remaining', 'مانده'),
                      t('On hand', 'موجودی'), t('Reserved', 'رزرو'), t('Unit cost', 'قیمت واحد'),
                      t('Location', 'محل'), t('Status', 'وضعیت'), ''].map(h =>
                      <th key={h} className="text-start px-2 py-1 font-normal whitespace-nowrap">{h}</th>)}
                  </tr>
                </thead>
                <tbody>
                  {detail.lots.map(l => (
                    <tr key={l.id} className="border-t border-slate-700/60">
                      <td className="px-2 py-1">
                        <input type="checkbox" checked={selected.has(l.id)}
                               onChange={e => setSelected(s => {
                                 const n = new Set(s)
                                 e.target.checked ? n.add(l.id) : n.delete(l.id)
                                 return n
                               })} />
                      </td>
                      <td className="px-2 py-1 font-mono">{l.lot_number}</td>
                      <td className={`px-2 py-1 whitespace-nowrap ${
                        (l.days_to_expiry ?? 1) < 0 ? 'text-rose-300'
                        : (l.days_to_expiry ?? 999) < 90 ? 'text-amber-300' : ''}`}>
                        {faDate(l.expiry_date)}
                      </td>
                      <td className="px-2 py-1 font-mono tabular-nums">
                        {l.days_to_expiry != null ? t(`${fa(l.days_to_expiry)} d`, `${fa(l.days_to_expiry)} روز`) : '—'}
                      </td>
                      <td className="px-2 py-1 font-mono tabular-nums">{fa(l.quantity_on_hand)}</td>
                      <td className="px-2 py-1 font-mono tabular-nums text-slate-400">{fa(l.quantity_reserved)}</td>
                      <td className="px-2 py-1 font-mono tabular-nums text-slate-400">{fa(l.unit_cost)}</td>
                      <td className="px-2 py-1">{l.storage_location || '—'}</td>
                      <td className="px-2 py-1">
                        {l.blocked_reason
                          ? <Badge tone="rose">{BLOCKED[l.blocked_reason]
                              ? tp(BLOCKED[l.blocked_reason]) : l.blocked_reason}</Badge>
                          : <span className="text-emerald-400">{t('Sellable', 'قابل عرضه')}</span>}
                      </td>
                      <td className="px-2 py-1 whitespace-nowrap">
                        <button onClick={() => editLot(l.id, 'storage_location', l.storage_location)}
                                className="text-indigo-300 hover:underline">{t('Relocate', 'جابجایی')}</button>
                        <span className="text-slate-600 px-1">·</span>
                        <button onClick={() => editLot(l.id, 'expiry_date', l.expiry_date)}
                                className="text-indigo-300 hover:underline">{t('Expiry', 'انقضا')}</button>
                        <span className="text-slate-600 px-1">·</span>
                        <button onClick={() => editLot(l.id, 'unit_cost', l.unit_cost)}
                                className="text-indigo-300 hover:underline">{t('Cost', 'قیمت')}</button>
                        <span className="text-slate-600 px-1">·</span>
                        <button onClick={() => writeOff(l)}
                                className="text-rose-300 hover:underline">{t('Write off', 'کسر')}</button>
                      </td>
                    </tr>))}
                </tbody>
              </table>
            </div>
            <p className="text-[10px] text-slate-500 pt-1">
              {t('“Write off” is a request, not an act — stock does not move until a second person approves. Extending an expiry date needs approval too.',
                 '«کسر» درخواست است، نه اجرا — تا تأیید نفر دوم موجودی تغییر نمی‌کند. تمدید تاریخ انقضا نیز به تأیید نیاز دارد.')}
            </p>
          </div>

          {/* movement history */}
          <div>
            <p className="text-[12px] font-semibold mb-1">{t('Full change history', 'سابقهٔ کامل تغییرات')}</p>
            {detail.movements.length === 0
              ? <p className="text-[11px] text-slate-500">{t('No movement has been recorded.', 'هیچ حرکتی ثبت نشده است.')}</p>
              : (
              <div className="overflow-x-auto max-h-72 overflow-y-auto">
                <table className="w-full text-[11px]">
                  <thead className="text-slate-500 text-[10px] sticky top-0 bg-slate-800">
                    <tr>{[t('Time', 'زمان'), t('Type', 'نوع'), t('Change', 'تغییر'),
                          t('From', 'از'), t('To', 'به'), t('User', 'کاربر'),
                          t('Reason', 'دلیل'), ''].map(h =>
                      <th key={h} className="text-start px-2 py-1 font-normal whitespace-nowrap">{h}</th>)}</tr>
                  </thead>
                  <tbody>
                    {detail.movements.map(m => (
                      <tr key={m.id} className="border-t border-slate-700/60">
                        <td className="px-2 py-1 whitespace-nowrap text-slate-400">
                          {dateTime(m.at)}
                        </td>
                        <td className="px-2 py-1 whitespace-nowrap">{MV[m.movement_type]
                          ? tp(MV[m.movement_type]) : m.movement_type}</td>
                        <td className={`px-2 py-1 font-mono tabular-nums ${
                          m.delta < 0 ? 'text-rose-300' : 'text-emerald-300'}`}>
                          {m.delta > 0 ? '+' : ''}{fa(m.delta)}
                        </td>
                        <td className="px-2 py-1 font-mono tabular-nums text-slate-500">{fa(m.before)}</td>
                        <td className="px-2 py-1 font-mono tabular-nums text-slate-500">{fa(m.after)}</td>
                        <td className="px-2 py-1 text-slate-400" title={m.actor_role || ''}>{m.actor || '—'}</td>
                        <td className="px-2 py-1 text-slate-400 max-w-xs truncate">{m.reason}</td>
                        <td className="px-2 py-1">
                          {m.chained && <span title={t('Recorded in the immutable chain', 'در زنجیرهٔ تغییرناپذیر ثبت شده')}>🔗</span>}
                          {m.approval_id && <span title={t('With a second person\u2019s approval', 'با تأیید نفر دوم')}>✍️</span>}
                        </td>
                      </tr>))}
                  </tbody>
                </table>
              </div>)}
          </div>
        </div>)}
    </div>
  )
}

function Badge({ tone, children }: { tone: 'rose' | 'amber' | 'sky' | 'slate'
                                     children: React.ReactNode }) {
  const cls = {
    rose: 'bg-rose-500/15 text-rose-300 border-rose-500/40',
    amber: 'bg-amber-500/15 text-amber-300 border-amber-500/40',
    sky: 'bg-sky-500/15 text-sky-300 border-sky-500/40',
    slate: 'bg-slate-600/20 text-slate-400 border-slate-600',
  }[tone]
  return <span className={`text-[10px] px-1.5 py-0.5 rounded border whitespace-nowrap ${cls}`}>{children}</span>
}

function ReceiveForm({ onDone }: { onDone: (m: { kind: 'ok' | 'err'; text: string }) => void }) {
  const { t, n: fa } = useLang()
  const [f, setF] = useState({ ndc11: '', lot_number: '', expiry_date: '',
                               quantity: '', unit_cost: '', storage_location: '' })
  const [busy, setBusy] = useState(false)
  const set = (k: string) => (e: React.ChangeEvent<HTMLInputElement>) =>
    setF(v => ({ ...v, [k]: e.target.value }))

  const submit = async () => {
    setBusy(true)
    try {
      const { data } = await inventoryAdminApi.receive({
        ndc11: f.ndc11.trim(), lot_number: f.lot_number.trim(),
        expiry_date: f.expiry_date, quantity: Number(f.quantity),
        unit_cost: f.unit_cost ? Number(f.unit_cost) : undefined,
        storage_location: f.storage_location || undefined,
      })
      onDone({ kind: 'ok', text: t(
        `${fa(Number(f.quantity))} units recorded into lot ${f.lot_number} (lot on hand: ${fa(data.lot_on_hand)}).`,
        `${fa(Number(f.quantity))} واحد در بچ ${f.lot_number} ثبت شد (موجودی بچ: ${fa(data.lot_on_hand)}).`) })
    } catch (e) { onDone({ kind: 'err', text: apiErrorText(e) }) }
    finally { setBusy(false) }
  }

  return (
    <div className="bg-slate-800/60 border border-emerald-700/50 rounded-lg p-4 space-y-2">
      <p className="text-sm font-semibold">{t('Receive goods', 'دریافت کالا')}</p>
      <p className="text-[11px] text-slate-500">
        {t('Every receipt writes a RECEIPT movement to the ledger. A past expiry date is refused.',
           'هر دریافت یک حرکت RECEIPT در دفتر ثبت می‌کند. تاریخ انقضای گذشته پذیرفته نمی‌شود.')}
      </p>
      <div className="flex flex-wrap gap-2 text-[12px]">
        <In label="NDC" v={f.ndc11} on={set('ndc11')} w="w-36" />
        <In label={t('Lot number', 'شماره بچ')} v={f.lot_number} on={set('lot_number')} w="w-32" />
        <In label={t('Expiry date', 'تاریخ انقضا')} v={f.expiry_date} on={set('expiry_date')} type="date" w="w-40" />
        <In label={t('Quantity', 'تعداد')} v={f.quantity} on={set('quantity')} type="number" w="w-24" />
        <In label={t('Unit cost', 'قیمت واحد')} v={f.unit_cost} on={set('unit_cost')} type="number" w="w-28" />
        <In label={t('Location', 'محل')} v={f.storage_location} on={set('storage_location')} w="w-32" />
        <button onClick={submit}
                disabled={busy || !f.ndc11 || !f.lot_number || !f.expiry_date || !f.quantity}
                className="self-end px-4 py-1.5 bg-emerald-600 hover:bg-emerald-500 rounded-lg disabled:opacity-40">
          {t('Record receipt', 'ثبت دریافت')}
        </button>
      </div>
    </div>
  )
}

function In({ label, v, on, type = 'text', w }: {
  label: string; v: string; on: (e: React.ChangeEvent<HTMLInputElement>) => void
  type?: string; w: string }) {
  return (
    <label className="flex flex-col gap-1">
      <span className="text-slate-500 text-[10px]">{label}</span>
      <input type={type} value={v} onChange={on}
             className={`${w} bg-slate-900 border border-slate-600 rounded px-2 py-1`} />
    </label>
  )
}
