/**
 * InventoryEngines — «موتورهای موجودی»
 *
 * The measured engines, in the order an operator uses them: what to decide,
 * what to count, what the demand signal actually says, and what the stock is
 * worth.
 *
 * One rule governs the whole panel: **a number is never shown without its
 * provenance.** Every rate here carries a basis — observed, sparse, no_history,
 * declared_default — and the UI renders that basis beside the figure rather
 * than under a tooltip. A demand rate displayed as a bare "5.0/day" is
 * indistinguishable from a measurement, and the seeded values that claimed 14
 * units/day for an item nobody dispenses looked exactly that convincing.
 *
 * There is deliberately no "apply all" on recommendations. Accepting is a
 * decision per item, and a bulk button would make the acceptance rate a measure
 * of how fast someone can click.
 */
import { useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { inventoryEnginesApi, apiErrorText } from '../lib/api'
import { useLang } from '../lib/i18n'

type Tab = 'advice' | 'pick' | 'count' | 'demand' | 'season' | 'value'
         | 'suppliers' | 'shortages'

type Pair = readonly [string, string]

const TABS: { id: Tab; label: Pair; hint: Pair }[] = [
  { id: 'advice', label: ['Advice', 'توصیه‌ها'], hint: ['What the engines propose, and your decision', 'پیشنهادهای موتورها و تصمیم شما'] },
  { id: 'count', label: ['Cycle counting', 'شمارش دوره‌ای'], hint: ['Where the counting hours go', 'کجا وقت شمارش صرف شود'] },
  { id: 'pick', label: ['Morning round', 'نوبت صبح'], hint: ['What to bring from the depot before opening', 'پیش از بازگشایی چه چیزی از انبار بیاورید'] },
  { id: 'demand', label: ['Demand signal', 'سیگنال تقاضا'], hint: ['The measured consumption rate', 'نرخ مصرف اندازه‌گیری‌شده'] },
  { id: 'season', label: ['Seasonality', 'فصلی بودن'], hint: ['Real annual patterns, by Jalali month — nothing claimed below two cycles', 'الگوهای واقعی سالانه بر پایهٔ ماه شمسی — زیر دو دوره ادعایی نمی‌شود'] },
  { id: 'shortages', label: ['Shortages', 'کمبودها'], hint: ['What is running out, and whether the market or one supplier is the cause', 'چه چیزی دارد تمام می‌شود و علت بازار است یا یک تأمین‌کننده'] },
  { id: 'suppliers', label: ['Suppliers', 'تأمین‌کنندگان'], hint: ['How long each one takes, and how much of an order turns up', 'هر کدام چقدر طول می‌کشند و چه مقدار از سفارش می‌رسد'] },
  { id: 'value', label: ['Value and shrinkage', 'ارزش و ضایعات'], hint: ['Stock value and the cost of losses', 'ارزش موجودی و بهای زیان'] },
]

/** How a number was arrived at. Shown, never hidden. */
const BASIS: Record<string, { label: Pair; cls: string; title: Pair }> = {
  observed: {
    label: ['Measured', 'اندازه‌گیری‌شده'], cls: 'bg-emerald-500/15 text-emerald-300 border-emerald-500/40',
    title: ['Computed from this pharmacy\u2019s own records', 'از سوابق واقعی این داروخانه محاسبه شده است'],
  },
  sparse: {
    label: ['Thin data', 'داده کم'], cls: 'bg-sky-500/15 text-sky-300 border-sky-500/40',
    title: ['Not enough data for a rate you can rely on; provisional',
            'داده‌های کافی برای یک نرخ قابل اتکا وجود ندارد؛ موقتی است'],
  },
  no_history: {
    label: ['No history', 'سابقه‌ای نیست'], cls: 'bg-slate-600/20 text-slate-400 border-slate-600',
    title: ['No consumption recorded; no number is guessed', 'هیچ مصرفی ثبت نشده؛ عددی حدس زده نمی‌شود'],
  },
  declared_default: {
    label: ['Declared assumption', 'فرض اعلام‌شده'], cls: 'bg-amber-500/15 text-amber-300 border-amber-500/40',
    title: ['Not measured; this is an explicit assumption, not an observation',
            'اندازه‌گیری نشده است؛ این یک فرض صریح است، نه مشاهده'],
  },
}

const SEV: Record<string, string> = {
  critical: 'bg-rose-500/15 text-rose-300 border-rose-500/40',
  high: 'bg-amber-500/15 text-amber-300 border-amber-500/40',
  medium: 'bg-sky-500/15 text-sky-300 border-sky-500/40',
  info: 'bg-slate-600/20 text-slate-300 border-slate-600',
}

const VERDICT: Record<string, { label: Pair; cls: string; note: Pair }> = {
  trusted: { label: ['Reliable', 'قابل اتکا'], cls: 'text-emerald-300',
             note: ['Most of its proposals are accepted', 'بیشتر پیشنهادها پذیرفته می‌شود'] },
  mixed: { label: ['Mixed', 'مختلط'], cls: 'text-sky-300', note: ['', ''] },
  noisy: { label: ['Noisy', 'پرنویز'], cls: 'text-amber-300',
           note: ['The threshold makes more work than value', 'آستانه بیش از ارزش، کار تولید می‌کند'] },
  ignored: {
    label: ['Being ignored', 'نادیده گرفته می‌شود'], cls: 'text-rose-300',
    note: ['Produces a lot and almost nothing is decided on it',
           'زیاد تولید می‌کند و تقریباً هیچ تصمیمی روی آن گرفته نمی‌شود'],
  },
  unmeasured: {
    label: ['Not yet measurable', 'هنوز سنجش‌پذیر نیست'], cls: 'text-slate-400',
    note: ['Too few decisions recorded to say', 'تصمیم‌های کافی برای اظهار نظر ثبت نشده است'],
  },
}

export default function InventoryEngines() {
  const { t, tp, dir } = useLang()
  const [tab, setTab] = useState<Tab>('advice')
  const [msg, setMsg] = useState<{ kind: 'ok' | 'err'; text: string } | null>(null)

  return (
    <div className="p-4 space-y-4 text-slate-100" dir={dir}>
      <div className="flex flex-wrap items-baseline gap-3">
        <h2 className="text-lg font-bold">{t('Inventory engines', 'موتورهای موجودی')}</h2>
        <span className="text-[11px] text-slate-500">
          {t('Every number is shown with where it came from; a rate that was never measured is never guessed.',
             'هر عدد با منشأ خود نمایش داده می‌شود؛ نرخی که اندازه‌گیری نشده باشد، حدس زده نمی‌شود.')}
        </span>
      </div>

      <div className="flex flex-wrap gap-1.5 border-b border-slate-700 pb-2">
        {TABS.map(tb => (
          <button key={tb.id} onClick={() => { setTab(tb.id); setMsg(null) }} title={tp(tb.hint)}
            className={`px-3 py-1.5 rounded-t text-[13px] border-b-2 -mb-[9px] transition-colors ${
              tab === tb.id
                ? 'border-sky-400 text-sky-300 bg-slate-800/60'
                : 'border-transparent text-slate-400 hover:text-slate-200'}`}>
            {tp(tb.label)}
          </button>
        ))}
      </div>

      {msg && (
        <p className={`text-sm ${msg.kind === 'ok' ? 'text-emerald-400' : 'text-red-400'}`}>
          {msg.text}
        </p>)}

      {tab === 'advice' && <AdviceTab onMsg={setMsg} />}
      {tab === 'count' && <CountTab />}
      {tab === 'demand' && <DemandTab onMsg={setMsg} />}
      {tab === 'pick' && <PickTab />}
      {tab === 'season' && <SeasonTab />}
      {tab === 'shortages' && <ShortageTab />}
      {tab === 'suppliers' && <SupplierTab />}
      {tab === 'value' && <ValueTab />}
    </div>
  )
}

/* ────────────────────────────────────────────────────────────────────────
   Recommendations — advice, and the scoreboard it has earned
   ──────────────────────────────────────────────────────────────────────── */

interface Rec {
  id: string; kind: string; ndc11: string | null; explanation: string
  severity: string | null; confidence: number | null; produced_by: string
  proposal: Record<string, unknown>; features: Record<string, unknown> | null
  created_at: string
}
interface Score {
  kind: string; produced: number; decided: number; accepted: number
  rejected: number; open: number; superseded: number
  acceptance_rate: number | null; verdict: string; explanation: string
}

function AdviceTab({ onMsg }: { onMsg: (m: { kind: 'ok' | 'err'; text: string }) => void }) {
  const { t, tp, n: fa } = useLang()
  const qc = useQueryClient()
  const [rejecting, setRejecting] = useState<string | null>(null)
  const [note, setNote] = useState('')
  const [busy, setBusy] = useState(false)

  const { data, isLoading, error } = useQuery<{ recommendations: Rec[]; count: number }>({
    queryKey: ['inv-recs'],
    queryFn: () => inventoryEnginesApi.recommendations({ status: 'open', limit: 100 })
      .then(r => r.data),
    refetchInterval: 60_000,
  })
  const { data: board } = useQuery<{ scores: Score[]; rejection_reasons: unknown[] }>({
    queryKey: ['inv-rec-score'],
    queryFn: () => inventoryEnginesApi.scoreboard().then(r => r.data),
    refetchInterval: 120_000,
  })

  const decide = async (id: string, accept: boolean, reason?: string) => {
    setBusy(true)
    try {
      await inventoryEnginesApi.decideRecommendation(id, { accept, note: reason })
      onMsg({
        kind: 'ok',
        text: accept
          ? t('Accepted. Carrying it out still goes through approval and the stock ledger.',
              'پذیرفته شد. اجرای آن همچنان از مسیر تأیید و دفتر موجودی انجام می‌شود.')
          : t('Rejected; your reason is recorded and will inform the threshold.',
              'رد شد؛ دلیل شما ثبت گردید و مبنای تنظیم آستانه خواهد بود.'),
      })
      setRejecting(null); setNote('')
      qc.invalidateQueries({ queryKey: ['inv-recs'] })
      qc.invalidateQueries({ queryKey: ['inv-rec-score'] })
    } catch (e: unknown) {
      onMsg({ kind: 'err', text: apiErrorText(e, t('The decision could not be recorded.', 'ثبت تصمیم ناموفق بود.')) })
    } finally { setBusy(false) }
  }

  return (
    <div className="space-y-4">
      {/* The scoreboard sits above the queue on purpose: an operator should see
          that a detector is being ignored before working through more of it. */}
      {board && board.scores.length > 0 && (
        <div className="bg-slate-800/40 border border-slate-700 rounded-lg px-4 py-3">
          <p className="text-[11px] text-slate-500 pb-2">
            {t('Engine scorecard — “acceptance” means the reviewer agreed, not that the result was right.',
               'کارنامهٔ موتورها — «پذیرش» یعنی موافقت کارشناس، نه درستی نتیجه.')}
          </p>
          <div className="flex flex-wrap gap-3">
            {board.scores.map(s => {
              const v = VERDICT[s.verdict] ?? VERDICT.unmeasured
              return (
                <div key={s.kind} title={s.explanation}
                  className="bg-slate-900/60 border border-slate-700 rounded px-3 py-2 min-w-[190px]">
                  <div className="flex items-baseline justify-between gap-3">
                    <span className="text-[12px] text-slate-300">{s.kind}</span>
                    <span className={`text-[12px] font-semibold ${v.cls}`}>{tp(v.label)}</span>
                  </div>
                  <div className="text-[11px] text-slate-500 tabular-nums pt-1">
                    {t(`${fa(s.produced)} proposed · ${fa(s.decided)} decided`,
                       `${fa(s.produced)} پیشنهاد · ${fa(s.decided)} تصمیم`)}
                    {s.acceptance_rate !== null &&
                      <> · {t(`${fa(Math.round(s.acceptance_rate * 100))}% accepted`,
                              `پذیرش ${fa(Math.round(s.acceptance_rate * 100))}٪`)}</>}
                  </div>
                  {tp(v.note) && <div className="text-[10px] text-slate-600 pt-0.5">{tp(v.note)}</div>}
                </div>)
            })}
          </div>
        </div>)}

      <div className="bg-slate-800/50 border border-slate-700 rounded-lg p-4 space-y-2">
        <div className="flex items-center gap-2">
          <span className="font-semibold text-sm">{t('Open proposals', 'پیشنهادهای باز')}</span>
          <span className="text-[11px] text-slate-500">
            {t('Rejecting requires a reason — your reason is the one thing that cannot be reconstructed later.',
               'رد کردن نیازمند دلیل است — دلیل شما تنها چیزی است که بعداً قابل بازسازی نیست.')}
          </span>
        </div>
        {isLoading && <p className="text-sm text-slate-400">{t('Loading…', 'در حال بارگذاری…')}</p>}
        {error && <p className="text-sm text-red-400">{apiErrorText(error)}</p>}
        {data && data.count === 0 && (
          <p className="text-[12px] text-slate-500">{t('There are no open proposals.', 'پیشنهاد بازی وجود ندارد.')}</p>)}

        {data?.recommendations.map(r => (
          <div key={r.id} className="border-t border-slate-700/60 pt-2 space-y-1.5">
            <div className="flex flex-wrap items-center gap-2 text-[12px]">
              <span className={`text-[10px] px-1.5 py-0.5 rounded border ${
                SEV[r.severity ?? 'info'] ?? SEV.info}`}>{r.kind}</span>
              {r.ndc11 && <span className="font-mono text-slate-400">{r.ndc11}</span>}
              <span className="text-slate-300">{r.explanation}</span>
              {r.confidence !== null && (
                <span className="text-[11px] text-slate-500 tabular-nums">
                  {t(`${fa(Math.round(r.confidence * 100))}% confidence`,
                     `اطمینان ${fa(Math.round(r.confidence * 100))}٪`)}
                </span>)}
              <span className="text-[10px] text-slate-600">{r.produced_by}</span>

              <div className="ms-auto flex gap-2">
                <button onClick={() => decide(r.id, true)} disabled={busy}
                  className="px-3 py-1 bg-emerald-600 hover:bg-emerald-500 rounded disabled:opacity-50">
                  {t('Accept', 'پذیرش')}
                </button>
                <button onClick={() => { setRejecting(rejecting === r.id ? null : r.id); setNote('') }}
                  disabled={busy}
                  className="px-3 py-1 bg-slate-700 hover:bg-slate-600 rounded disabled:opacity-50">
                  {t('Reject', 'رد')}
                </button>
              </div>
            </div>

            {rejecting === r.id && (
              <div className="flex flex-wrap items-center gap-2 pb-1">
                <input autoFocus value={note} onChange={e => setNote(e.target.value)}
                  placeholder={t('Why is this proposal wrong?', 'چرا این پیشنهاد درست نیست؟')}
                  className="flex-1 min-w-[240px] bg-slate-900 border border-slate-700 rounded
                             px-2 py-1 text-[12px] focus:outline-none focus:border-sky-500" />
                <button disabled={busy || !note.trim()}
                  onClick={() => decide(r.id, false, note.trim())}
                  className="px-3 py-1 bg-rose-600 hover:bg-rose-500 rounded text-[12px]
                             disabled:opacity-40">
                  {t('Record rejection', 'ثبت رد')}
                </button>
                {!note.trim() && (
                  <span className="text-[10px] text-slate-500">{t('A reason is required', 'دلیل الزامی است')}</span>)}
              </div>)}
          </div>))}
      </div>
    </div>
  )
}

/* ────────────────────────────────────────────────────────────────────────
   Cycle count — where the counting hours go
   ──────────────────────────────────────────────────────────────────────── */

interface CountLine {
  ndc11: string; class: string; interval_days: number
  last_counted: string | null; days_since: number | null
  overdue_by: number; priority: number; risks: string[]; reason: string
}
interface CountPlan {
  as_of: string; capacity: number; items_classified: number
  class_mix: Record<string, number>; due_now: number
  session: {
    lines: CountLine[]; counted: number; deferred: number
    deferred_classes: string[]; worst_deferred: CountLine | null
    coverage_note: string
  }
  effort: {
    ranked_lines: number; flat_lines: number; difference: number
    pct_change: number; a_class_coverage_gain: number; note: string
  }
}

const RISK: Record<string, Pair> = {
  controlled: ['Controlled', 'تحت کنترل'],
  recent_variance: ['Recent variance', 'مغایرت اخیر'],
  expiring: ['Expiring soon', 'نزدیک انقضا'],
  unpredictable: ['Irregular consumption', 'مصرف نامنظم'],
  high_value_lot: ['High-value lot', 'بچ پرارزش'],
}

function CountTab() {
  const { t, tp, n: fa } = useLang()
  const [capacity, setCapacity] = useState(25)
  const { data, isLoading, error } = useQuery<CountPlan>({
    queryKey: ['inv-cyclecount', capacity],
    queryFn: () => inventoryEnginesApi.cycleCountPlan(capacity).then(r => r.data),
  })

  return (
    <div className="space-y-4">
      <div className="bg-slate-800/50 border border-slate-700 rounded-lg p-4
                      flex flex-wrap items-center gap-x-8 gap-y-3">
        <label className="flex items-center gap-2 text-[12px]">
          <span className="text-slate-400">{t('Capacity per session', 'ظرفیت هر نوبت')}</span>
          <input type="number" min={1} max={500} value={capacity}
            onChange={e => setCapacity(Math.max(1, Number(e.target.value) || 1))}
            className="w-20 bg-slate-900 border border-slate-700 rounded px-2 py-1
                       tabular-nums focus:outline-none focus:border-sky-500" />
        </label>
        {data && <>
          <Metric label={t('Items classified', 'اقلام طبقه‌بندی‌شده')} value={fa(data.items_classified)} />
          <Metric label={t('Due now', 'اکنون سررسید')} value={fa(data.due_now)} />
          <Metric label={t('In this session', 'در این نوبت')} value={fa(data.session.counted)} />
          <Metric label={t('Deferred', 'موکول‌شده')} value={fa(data.session.deferred)} />
        </>}
      </div>

      {isLoading && <p className="text-sm text-slate-400">{t('Calculating…', 'در حال محاسبه…')}</p>}
      {error && <p className="text-sm text-red-400">{apiErrorText(error)}</p>}

      {data && (
        <>
          {/* The comparison is reported, not claimed. A risk-ranked schedule can
              cost more than the flat sweep it replaces, and that must be visible. */}
          <div className="bg-slate-800/40 border border-slate-700 rounded-lg px-4 py-3">
            <p className="text-[11px] text-slate-500 pb-2">
              {t('Compared with a flat 90-day count', 'در مقایسه با شمارش یکنواخت ۹۰ روزه')}
            </p>
            <div className="flex flex-wrap items-center gap-x-8 gap-y-2">
              <Metric label={t('Count lines (this method)', 'خطوط شمارش (این روش)')} value={fa(data.effort.ranked_lines)} />
              <Metric label={t('Count lines (flat)', 'خطوط شمارش (یکنواخت)')} value={fa(data.effort.flat_lines)} />
              <div>
                <div className="text-[10px] text-slate-500">{t('Change', 'تغییر')}</div>
                <div className={`text-lg font-bold tabular-nums ${
                  data.effort.pct_change <= 0 ? 'text-emerald-300' : 'text-amber-300'}`}>
                  {data.effort.pct_change > 0 ? '+' : ''}{fa(data.effort.pct_change)}{t('%', '٪')}
                </div>
              </div>
              <div>
                <div className="text-[10px] text-slate-500">{t('Extra attention on class-A items', 'توجه بیشتر روی اقلام کلاس A')}</div>
                <div className="text-lg font-bold tabular-nums text-sky-300">
                  {data.effort.a_class_coverage_gain > 0 ? '+' : ''}
                  {fa(data.effort.a_class_coverage_gain)}
                </div>
              </div>
            </div>
            {data.effort.pct_change > 0 && (
              <p className="text-[11px] text-amber-300/80 pt-2">
                {t('This schedule asks for more count lines than the flat sweep. The saving comes from a tail of low-value items, and a small catalogue has no such tail; what happens instead is a shift of attention onto the valuable items.',
                   'این برنامه خطوط شمارش بیشتری از روش یکنواخت می‌خواهد. صرفه‌جویی از دنبالهٔ اقلام کم‌ارزش می‌آید و در فهرست‌های کوچک چنین دنباله‌ای وجود ندارد؛ آنچه اکنون رخ می‌دهد جابه‌جایی توجه به سمت اقلام پرارزش است.')}
              </p>)}
          </div>

          <div className="bg-slate-800/50 border border-slate-700 rounded-lg p-4 space-y-2">
            <div className="flex flex-wrap items-center gap-2">
              <span className="font-semibold text-sm">{t('Next count sheet', 'برگهٔ شمارش بعدی')}</span>
              <span className="text-[11px] text-slate-500">{data.session.coverage_note}</span>
            </div>
            {data.session.lines.length === 0 && (
              <p className="text-[12px] text-slate-500">{t('Nothing is due.', 'موردی سررسید نشده است.')}</p>)}
            {data.session.lines.map(l => (
              <div key={l.ndc11}
                className="flex flex-wrap items-center gap-3 border-t border-slate-700/60 pt-2 text-[12px]">
                <span className="font-mono text-slate-300">{l.ndc11}</span>
                <span className="text-[10px] px-1.5 py-0.5 rounded bg-slate-700/50
                                 border border-slate-600 text-slate-300">{l.class}</span>
                <span className="text-slate-500">{t(`every ${fa(l.interval_days)} days`, `هر ${fa(l.interval_days)} روز`)}</span>
                <span className="text-slate-400">{l.reason}</span>
                {l.risks.map(r => (
                  <span key={r} className="text-[10px] px-1.5 py-0.5 rounded
                                           bg-amber-500/15 text-amber-300 border border-amber-500/40">
                    {RISK[r] ? tp(RISK[r]) : r}
                  </span>))}
              </div>))}
            {data.session.worst_deferred && (
              <p className="text-[11px] text-slate-500 pt-2 border-t border-slate-700/60">
                {t('Most important deferred item', 'مهم‌ترین مورد موکول‌شده')}: <span className="font-mono">
                {data.session.worst_deferred.ndc11}</span> — {data.session.worst_deferred.reason}
              </p>)}
          </div>
        </>)}
    </div>
  )
}

/* ────────────────────────────────────────────────────────────────────────
   Demand signal — measured, or explicitly not
   ──────────────────────────────────────────────────────────────────────── */

interface RefreshRow {
  ndc11: string; stored_adq: number | null; new_adq: number | null
  basis: string; confidence: number; units_observed: number
  window_days: number; changed: boolean; verdict: string; explanation: string
  supplier?: string | null
  signals: {
    reorder_point: number | null; safety_stock: number | null
    lead_time_basis: string; lead_time_days: number; explanation: string
    demand_class: string; event_floor: number | null; floor_applied: boolean
  }
  // E6 — the shape behind the rate.
  pattern: {
    demand_class: string; adi: number | null; cv2: number | null
    typical_event: number | null; largest_event: number | null
    mean_rate: number | null; drifting: boolean; direction: string
    earlier_rate: number | null; recent_rate: number | null
    forecastable: boolean; basis: string; concerns: string[]
    explanation: string
  }
}
interface Refresh {
  applied: boolean; window_days: number; as_of: string
  lead_time: { days: number; basis: string; samples: number; explanation: string }
  rows: RefreshRow[]; written: number
  summary: {
    items: number; changed: number; by_basis: Record<string, number>
    prior_signal_verdict: Record<string, number>; reorder_points_set: number
    by_demand_class: Record<string, number>; covered_for_one_event: number
  }
}

/**
 * The shape of an item's demand, which a rate cannot express.
 *
 * Two units a day and forty units once every three weeks are the same rate and
 * want completely different reorder points. `lumpy` is deliberately worded as a
 * limitation rather than a category: rare events of wildly varying size cannot
 * be forecast well by anything, and the useful output there is the size of one
 * event, not a daily figure.
 */
const DEMAND_CLASS: Record<string, { label: Pair; cls: string; title: Pair }> = {
  smooth: {
    label: ['Steady', 'یکنواخت'], cls: 'bg-emerald-500/15 text-emerald-300 border-emerald-500/40',
    title: ['Moves most days in steady quantities — a rate describes it well',
            'بیشتر روزها با مقدار ثابت مصرف می‌شود — نرخ آن را به‌خوبی توصیف می‌کند'],
  },
  intermittent: {
    label: ['In bursts', 'دوره‌ای'], cls: 'bg-sky-500/15 text-sky-300 border-sky-500/40',
    title: ['Moves in bursts with quiet stretches — cover has to serve a whole burst',
            'به‌صورت دوره‌ای با فاصله‌های خالی مصرف می‌شود — پوشش باید یک دورهٔ کامل را جواب دهد'],
  },
  erratic: {
    label: ['Uneven', 'ناهموار'], cls: 'bg-amber-500/15 text-amber-300 border-amber-500/40',
    title: ['Moves most days but in unpredictable quantities',
            'بیشتر روزها مصرف می‌شود اما با مقادیر غیرقابل پیش‌بینی'],
  },
  lumpy: {
    label: ['Cannot be forecast', 'قابل پیش‌بینی نیست'], cls: 'bg-rose-500/15 text-rose-300 border-rose-500/40',
    title: ['Rare and unpredictable in both timing and size — no method forecasts this well, so plan cover for one event rather than a daily rate',
            'هم در زمان و هم در مقدار نادر و غیرقابل پیش‌بینی — هیچ روشی این را خوب پیش‌بینی نمی‌کند؛ به‌جای نرخ روزانه، پوشش یک نوبت را برنامه‌ریزی کنید'],
  },
  unknown: {
    label: ['Not yet classified', 'هنوز طبقه‌بندی نشده'], cls: 'bg-slate-600/20 text-slate-400 border-slate-600',
    title: ['Too few demand events to describe a shape', 'رویدادهای مصرف برای توصیف الگو کافی نیست'],
  },
}

function ClassChip({ cls }: { cls: string }) {
  const { tp } = useLang()
  const c = DEMAND_CLASS[cls] ?? DEMAND_CLASS.unknown
  return (
    <span title={tp(c.title)}
      className={`text-[10px] px-1.5 py-0.5 rounded border whitespace-nowrap ${c.cls}`}>
      {tp(c.label)}
    </span>)
}

const PRIOR_VERDICT: Record<string, { label: Pair; cls: string }> = {
  contradicted: { label: ['Contradicted by the record', 'با سوابق در تضاد'], cls: 'text-rose-300' },
  understated: { label: ['Understated', 'کمتر از واقع'], cls: 'text-amber-300' },
  overstated: { label: ['Overstated', 'بیش از واقع'], cls: 'text-sky-300' },
  agrees: { label: ['Agrees', 'هم‌خوان'], cls: 'text-emerald-300' },
  indeterminate: { label: ['—', '—'], cls: 'text-slate-500' },
}

function DemandTab({ onMsg }: { onMsg: (m: { kind: 'ok' | 'err'; text: string }) => void }) {
  const { t, tp, n: fa } = useLang()
  const qc = useQueryClient()
  const [windowDays, setWindowDays] = useState(28)
  const [busy, setBusy] = useState(false)

  const { data, isLoading, error } = useQuery<Refresh>({
    queryKey: ['inv-demand', windowDays],
    queryFn: () => inventoryEnginesApi.refreshDemand({ apply: false, window_days: windowDays })
      .then(r => r.data),
  })

  const apply = async () => {
    setBusy(true)
    try {
      const r = await inventoryEnginesApi.refreshDemand({ apply: true, window_days: windowDays })
      onMsg({ kind: 'ok', text: t(`Consumption rate rewritten for ${fa(r.data.written)} items.`,
                                  `نرخ مصرف برای ${fa(r.data.written)} قلم بازنویسی شد.`) })
      qc.invalidateQueries({ queryKey: ['inv-demand'] })
      qc.invalidateQueries({ queryKey: ['inv-reconciliation'] })
    } catch (e: unknown) {
      onMsg({ kind: 'err', text: apiErrorText(e, t('Recalculation failed.', 'بازمحاسبه ناموفق بود.')) })
    } finally { setBusy(false) }
  }

  return (
    <div className="space-y-4">
      <div className="bg-slate-800/50 border border-slate-700 rounded-lg p-4
                      flex flex-wrap items-center gap-x-8 gap-y-3">
        <label className="flex items-center gap-2 text-[12px]">
          <span className="text-slate-400">{t('Observation window (days)', 'پنجرهٔ مشاهده (روز)')}</span>
          <input type="number" min={7} max={365} value={windowDays}
            onChange={e => setWindowDays(Math.min(365, Math.max(7, Number(e.target.value) || 7)))}
            className="w-20 bg-slate-900 border border-slate-700 rounded px-2 py-1
                       tabular-nums focus:outline-none focus:border-sky-500" />
        </label>
        {data && <>
          <Metric label={t('Items', 'اقلام')} value={fa(data.summary.items)} />
          <Metric label={t('Will change', 'تغییر می‌کند')} value={fa(data.summary.changed)} />
          <Metric label={t('Reorder point determinable', 'نقطهٔ سفارش قابل تعیین')} value={fa(data.summary.reorder_points_set)} />
          <Metric label={t('Cannot be forecast', 'قابل پیش‌بینی نیست')}
                  value={fa(data.summary.by_demand_class?.lumpy ?? 0)} />
          <Metric label={t('Covered for one event', 'پوشش یک نوبت')}
                  value={fa(data.summary.covered_for_one_event ?? 0)} />
          <div>
            <div className="text-[10px] text-slate-500">{t('Lead time', 'زمان تدارک')}</div>
            <div className="flex items-center gap-1.5">
              <span className="text-lg font-bold tabular-nums">{fa(data.lead_time.days)}</span>
              <BasisChip basis={data.lead_time.basis} />
            </div>
          </div>
          <button onClick={apply} disabled={busy}
            className="ms-auto px-4 py-1.5 bg-sky-600 hover:bg-sky-500 rounded
                       text-[13px] disabled:opacity-50">
            {t('Recalculate and write', 'بازمحاسبه و ثبت')}
          </button>
        </>}
      </div>

      {isLoading && <p className="text-sm text-slate-400">{t('Calculating…', 'در حال محاسبه…')}</p>}
      {error && <p className="text-sm text-red-400">{apiErrorText(error)}</p>}

      {data && (
        <div className="bg-slate-800/50 border border-slate-700 rounded-lg p-4">
          <p className="text-[11px] text-slate-500 pb-2">
            {t('Preview. The “prior value” column says how the stored number stood against the dispense record — not just what it becomes.',
               'پیش‌نمایش. ستون «وضعیت مقدار قبلی» می‌گوید عدد ذخیره‌شده در برابر سوابق تحویل چه وضعی داشته است — نه فقط اینکه به چه چیزی تبدیل می‌شود.')}
          </p>
          <div className="overflow-x-auto">
            <table className="w-full text-[12px]">
              <thead className="text-slate-500 text-[11px]">
                <tr className="border-b border-slate-700">
                  <th className="text-start font-normal py-1.5">{t('Item', 'قلم')}</th>
                  <th className="text-start font-normal">{t('Current value', 'مقدار فعلی')}</th>
                  <th className="text-start font-normal">{t('New value', 'مقدار جدید')}</th>
                  <th className="text-start font-normal">{t('Basis', 'منشأ')}</th>
                  <th className="text-start font-normal">{t('Shape', 'الگو')}</th>
                  <th className="text-start font-normal">{t('Observed consumption', 'مصرف مشاهده‌شده')}</th>
                  <th className="text-start font-normal">{t('Reorder point', 'نقطهٔ سفارش')}</th>
                  <th className="text-start font-normal">{t('Prior value', 'وضعیت مقدار قبلی')}</th>
                </tr>
              </thead>
              <tbody>
                {data.rows.map(r => {
                  const v = PRIOR_VERDICT[r.verdict] ?? PRIOR_VERDICT.indeterminate
                  return (
                    <tr key={r.ndc11} className="border-b border-slate-800/60">
                      <td className="py-1.5 font-mono text-slate-300">{r.ndc11}</td>
                      <td className="tabular-nums text-slate-400">
                        {r.stored_adq === null ? '—' : fa(r.stored_adq)}</td>
                      <td className="tabular-nums">
                        {r.new_adq === null
                          ? <span className="text-slate-500">{t('not measured', 'اندازه‌گیری نشد')}</span>
                          : <span className="text-slate-100 font-semibold">{fa(r.new_adq)}</span>}
                      </td>
                      <td><BasisChip basis={r.basis} /></td>
                      <td className="whitespace-nowrap">
                        <ClassChip cls={r.pattern?.demand_class ?? 'unknown'} />
                        {r.pattern?.typical_event != null
                         && r.pattern.demand_class !== 'smooth'
                         && r.pattern.demand_class !== 'unknown' && (
                          <span className="text-[10px] text-slate-500 ms-1 tabular-nums"
                                title={tp(['One typical demand event. Cover has to serve this, not the daily average.',
                                           'یک نوبت معمول مصرف. پوشش باید همین را جواب دهد، نه میانگین روزانه.'])}>
                            ×{fa(r.pattern.typical_event)}</span>)}
                        {r.pattern?.drifting && (
                          <span className="text-[10px] text-amber-300 ms-1"
                                title={r.pattern.concerns.join(' · ')}>
                            {r.pattern.direction === 'falling' ? '↓' : '↑'}</span>)}
                      </td>
                      <td className="tabular-nums text-slate-400">
                        {t(`${fa(r.units_observed)} in ${fa(r.window_days)} d`,
                           `${fa(r.units_observed)} در ${fa(r.window_days)} روز`)}</td>
                      <td className="tabular-nums text-slate-400">
                        {r.signals?.reorder_point === null || r.signals?.reorder_point === undefined
                          ? <span className="text-slate-600" title={r.signals?.explanation}>—</span>
                          : fa(Math.round(r.signals.reorder_point))}
                      </td>
                      <td className={v.cls} title={r.explanation}>{tp(v.label)}</td>
                    </tr>)
                })}
              </tbody>
            </table>
          </div>
        </div>)}
    </div>
  )
}

/* ────────────────────────────────────────────────────────────────────────
   Valuation and shrinkage — in currency, because units hide the difference
   ──────────────────────────────────────────────────────────────────────── */

interface Valuation {
  valuation: {
    method: string; total: number; uncosted_units: number; uncosted_items: number
    buckets: Record<string, number>; coverage_note: string
    items: { ndc11: string; on_hand: number; value: number; unit_cost: number | null
             lots_costed: number; lots_uncosted: number; explanation: string }[]
  }
  shrinkage: {
    period_days: number; total_lost: number; recoverable: number
    by_type: Record<string, number>; by_item: { ndc11: string; value: number }[]
    explanation: string
  }
  cost_basis_note: string
}

const BUCKET: Record<string, Pair> = {
  damaged: ['Damaged', 'آسیب‌دیده'], returned: ['Returned', 'مرجوعی'], in_transit: ['In transit', 'در راه'],
}

function ValueTab() {
  const { t, tp, n, money: faMoney } = useLang()
  const fa = n
  const [method, setMethod] = useState<'fifo' | 'weighted'>('fifo')
  const { data, isLoading, error } = useQuery<Valuation>({
    queryKey: ['inv-valuation', method],
    queryFn: () => inventoryEnginesApi.valuation({ method, shrinkage_days: 90 })
      .then(r => r.data),
  })

  return (
    <div className="space-y-4">
      <div className="bg-slate-800/50 border border-slate-700 rounded-lg p-4
                      flex flex-wrap items-center gap-x-8 gap-y-3">
        <label className="flex items-center gap-2 text-[12px]">
          <span className="text-slate-400">{t('Method', 'روش')}</span>
          <select value={method} onChange={e => setMethod(e.target.value as 'fifo' | 'weighted')}
            className="bg-slate-900 border border-slate-700 rounded px-2 py-1
                       focus:outline-none focus:border-sky-500">
            <option value="fifo">{t('First in, first out', 'اولین‌صادره از اولین‌وارده')}</option>
            <option value="weighted">{t('Weighted average', 'میانگین موزون')}</option>
          </select>
        </label>
        {data && <>
          <Metric label={t('Sellable stock value', 'ارزش موجودی قابل فروش')} value={faMoney(data.valuation.total)} />
          {Object.entries(data.valuation.buckets).map(([k, v]) => (
            <Metric key={k} label={BUCKET[k] ? tp(BUCKET[k]) : k} value={faMoney(v)} />))}
        </>}
      </div>

      {isLoading && <p className="text-sm text-slate-400">{t('Calculating…', 'در حال محاسبه…')}</p>}
      {error && <p className="text-sm text-red-400">{apiErrorText(error)}</p>}

      {data && (
        <>
          {data.valuation.uncosted_items > 0 && (
            <p className="text-[12px] text-amber-300/90 bg-amber-500/5 border border-amber-500/25
                          rounded px-3 py-2">
              {data.valuation.coverage_note}
            </p>)}

          <div className="bg-slate-800/50 border border-slate-700 rounded-lg p-4 space-y-2">
            <div className="flex flex-wrap items-baseline gap-x-8 gap-y-2">
              <span className="font-semibold text-sm">{t(`Shrinkage over the last ${fa(data.shrinkage.period_days)} days`,
                                                          `ضایعات ${fa(data.shrinkage.period_days)} روز اخیر`)}</span>
              <Metric label={t('Lost', 'از دست رفته')} value={faMoney(data.shrinkage.total_lost)} />
              <Metric label={t('Recoverable from the supplier', 'قابل بازیافت از تأمین‌کننده')} value={faMoney(data.shrinkage.recoverable)} />
            </div>
            <p className="text-[11px] text-slate-500">{data.shrinkage.explanation}</p>
            <div className="flex flex-wrap gap-1.5 pt-1">
              {Object.entries(data.shrinkage.by_type).map(([k, v]) => (
                <span key={k} className="text-[11px] px-2 py-1 rounded-full border
                                         bg-slate-900 border-slate-700 text-slate-300">
                  {k} <span className="font-mono tabular-nums">{faMoney(v)}</span>
                </span>))}
            </div>
          </div>

          <div className="bg-slate-800/50 border border-slate-700 rounded-lg p-4">
            <p className="text-[11px] text-slate-500 pb-2">{t('Most valuable items', 'پرارزش‌ترین اقلام')}</p>
            <div className="overflow-x-auto">
              <table className="w-full text-[12px]">
                <thead className="text-slate-500 text-[11px]">
                  <tr className="border-b border-slate-700">
                    <th className="text-start font-normal py-1.5">{t('Item', 'قلم')}</th>
                    <th className="text-start font-normal">{t('On hand', 'موجودی')}</th>
                    <th className="text-start font-normal">{t('Unit cost', 'بهای واحد')}</th>
                    <th className="text-start font-normal">{t('Value', 'ارزش')}</th>
                    <th className="text-start font-normal">{t('Uncosted lots', 'بچ‌های بدون بها')}</th>
                  </tr>
                </thead>
                <tbody>
                  {data.valuation.items.slice(0, 20).map(i => (
                    <tr key={i.ndc11} className="border-b border-slate-800/60">
                      <td className="py-1.5 font-mono text-slate-300">{i.ndc11}</td>
                      <td className="tabular-nums text-slate-400">{fa(i.on_hand)}</td>
                      <td className="tabular-nums text-slate-400">
                        {i.unit_cost === null ? '—' : faMoney(i.unit_cost)}</td>
                      <td className="tabular-nums font-semibold">{faMoney(i.value)}</td>
                      <td className="tabular-nums">
                        {i.lots_uncosted
                          ? <span className="text-amber-300" title={i.explanation}>
                              {fa(i.lots_uncosted)}</span>
                          : <span className="text-slate-600">{fa(0)}</span>}
                      </td>
                    </tr>))}
                </tbody>
              </table>
            </div>
            <p className="text-[10px] text-slate-600 pt-2">{data.cost_basis_note}</p>
          </div>
        </>)}
    </div>
  )
}

/* ────────────────────────────────────────────────────────────────────────
   Suppliers — E11 lead time and E12 reliability

   The column order carries the argument. **Delivered** comes first because it
   is what a short delivery costs: a sale. **Predictable** comes second, and
   **Typical days** last and deliberately unscored — a supplier that takes
   eleven days every time is already handled, because those eleven days are in
   the reorder point. Ranking by speed would push the pharmacy towards whoever
   is quickest on a good week.

   An unmeasured supplier shows a dash, never a score, and sorts to the bottom.
   A blank cell at the top of a table gets read as a clean record.
   ──────────────────────────────────────────────────────────────────────── */

interface Supplier {
  supplier: string; orders: number; lines: number
  fill_rate: number | null; fill_basis: string
  short_lines: number; short_line_rate: number | null; outstanding_lines: number
  lead_days: number; lead_basis: string; lead_stdev: number | null
  consistency: number | null
  score: number | null; basis: string; grade: string | null
  substitutions: number; substitution_basis: string
  concerns: string[]; products: number; explanation: string
  lead_time: { days: number; basis: string; samples: number
               median_days: number | null; stdev_days: number | null
               explanation: string }
}
interface Scorecard {
  suppliers: Supplier[]; measured: number
  orders_delivered: number; orders_outstanding: number
  head_to_head: { a: string; b: string; shared_products: number
                  verdict: string; explanation: string } | null
  explanation: string
}

const GRADE: Record<string, { label: Pair; cls: string }> = {
  dependable: { label: ['Dependable', 'قابل اتکا'], cls: 'bg-emerald-500/15 text-emerald-300 border-emerald-500/40' },
  workable: { label: ['Workable', 'قابل قبول'], cls: 'bg-sky-500/15 text-sky-300 border-sky-500/40' },
  mixed: { label: ['Mixed', 'مختلط'], cls: 'bg-amber-500/15 text-amber-300 border-amber-500/40' },
  poor: { label: ['Poor', 'ضعیف'], cls: 'bg-rose-500/15 text-rose-300 border-rose-500/40' },
}

function SupplierTab() {
  const { t, tp, n: fa } = useLang()
  const [briefFor, setBriefFor] = useState<string | null>(null)
  const { data, isLoading, error } = useQuery<Scorecard>({
    queryKey: ['inv-suppliers'],
    queryFn: () => inventoryEnginesApi.suppliers().then(r => r.data),
  })
  const pct = (v: number | null) => v === null ? '—' : `${fa(Math.round(v * 100))}٪`

  return (
    <div className="space-y-4">
      <p className="text-[11px] text-slate-500">
        {t('How long a supplier takes is reported but not scored — a predictable eleven days is already in the reorder point. What is scored is how much of an order arrives, and how predictably.',
           'مدت زمان تحویل گزارش می‌شود اما امتیاز نمی‌گیرد — یازده روزِ قابل پیش‌بینی از پیش در نقطهٔ سفارش لحاظ شده است. آنچه امتیاز می‌گیرد این است که چه مقدار از سفارش می‌رسد و با چه میزان پیش‌بینی‌پذیری.')}
      </p>

      {isLoading && <p className="text-sm text-slate-400">{t('Calculating…', 'در حال محاسبه…')}</p>}
      {error && <p className="text-sm text-red-400">{apiErrorText(error)}</p>}

      {data && (<>
        <div className="bg-slate-800/50 border border-slate-700 rounded-lg p-4
                        flex flex-wrap items-center gap-x-8 gap-y-3">
          <Metric label={t('Suppliers measured', 'تأمین‌کنندگان سنجیده‌شده')}
                  value={`${fa(data.measured)} / ${fa(data.suppliers.length)}`} />
          <Metric label={t('Orders delivered', 'سفارش‌های تحویل‌شده')} value={fa(data.orders_delivered)} />
          <Metric label={t('Still open', 'هنوز باز')} value={fa(data.orders_outstanding)} />
          <p className="text-[11px] text-slate-500 max-w-lg">{data.explanation}</p>
        </div>

        {data.head_to_head && (
          <p className={`text-[12px] rounded border px-3 py-2 ${
            data.head_to_head.verdict === 'not_comparable' || data.head_to_head.verdict === 'too_close'
              ? 'border-slate-600 bg-slate-800/40 text-slate-300'
              : 'border-emerald-600/40 bg-emerald-500/10 text-emerald-200'}`}>
            {data.head_to_head.explanation}
          </p>)}

        {data.suppliers.length === 0 && (
          <p className="text-sm text-slate-400">
            {t('No purchase orders yet. Nothing here can be measured until deliveries are received against an order.',
               'هنوز سفارش خریدی ثبت نشده است. تا زمانی که دریافت‌ها در برابر یک سفارش ثبت نشوند، چیزی اینجا سنجش‌پذیر نیست.')}
          </p>)}

        <div className="space-y-2">
          {data.suppliers.map(s => (
            <div key={s.supplier}
                 className={`bg-slate-800/50 border rounded-lg p-3 space-y-2 ${
                   s.score === null ? 'border-slate-700/60' : 'border-slate-700'}`}>
              <div className="flex flex-wrap items-baseline gap-x-6 gap-y-2">
                <span className="font-semibold">{s.supplier}</span>
                {s.grade
                  ? <span className={`text-[10px] px-1.5 py-0.5 rounded border ${GRADE[s.grade]?.cls ?? ''}`}>
                      {GRADE[s.grade] ? tp(GRADE[s.grade].label) : s.grade}</span>
                  : <span className="text-[10px] px-1.5 py-0.5 rounded border bg-slate-600/20 text-slate-400 border-slate-600">
                      {t('Not yet gradable', 'هنوز قابل درجه‌بندی نیست')}</span>}
                <Metric label={t('Delivered', 'تحویل‌شده')} value={pct(s.fill_rate)} />
                <Metric label={t('Predictable', 'پیش‌بینی‌پذیر')} value={pct(s.consistency)} />
                <Metric label={t('Typical days', 'روز معمول')}
                        value={s.lead_time.basis === 'declared_default' ? '—' : fa(s.lead_days)} />
                <Metric label={t('Orders', 'سفارش‌ها')} value={fa(s.orders)} />
                <BasisChip basis={s.lead_time.basis} />
              </div>
              <p className="text-[11px] text-slate-400">{s.explanation}</p>
              {s.concerns.map((c, i) => (
                <p key={i} className="text-[11px] text-amber-300/90">• {c}</p>))}
              <button onClick={() => setBriefFor(briefFor === s.supplier ? null : s.supplier)}
                className="text-[11px] px-2 py-0.5 rounded border border-sky-600/50
                           text-sky-300 hover:bg-sky-500/10">
                {briefFor === s.supplier
                  ? t('Hide brief', 'بستن خلاصه')
                  : t('Prepare for a conversation', 'آماده‌سازی برای گفت‌وگو')}
              </button>
              {briefFor === s.supplier && <NegotiationBrief supplier={s.supplier} />}
            </div>))}
        </div>
      </>)}
    </div>
  )
}

/* ────────────────────────────────────────────────────────────────────────
   ⑳ — the brief, and what it refuses to say

   Pulled, never pushed: the owner opens it before a conversation. The
   `cannot_say` list is rendered as prominently as the asks, on purpose. The
   failure this engine is arranged around is not a wrong figure on a screen — it
   is the owner repeating a fabricated market rate to a distributor who knows the
   real one, and losing the conversation in the first minute. What the brief does
   not know has to be as visible as what it does.
   ──────────────────────────────────────────────────────────────────────── */

interface BriefData {
  supplier: string
  leverage: { spend: number; share: number | null; lines: number
              molecules: number; standing: string; explanation: string }
  gaps: { ndc11: string; our_cost: number; best_cost: number
          best_supplier: string; gap_per_unit: number; gap_pct: number
          annual_units: number; annual_value: number }[]
  negotiable_annual: number
  reliability: { undelivered_units: number; forgone_margin: number | null
                 basis: string; explanation: string }
  asks: { ask: string; worth: number | null; basis: string; explanation: string }[]
  concessions: { ask: string; worth: number | null; basis: string
                 explanation: string }[]
  cannot_say: string[]
  basis: string
  explanation: string
  cloud: { available: boolean; reason?: string }
}

function NegotiationBrief({ supplier }: { supplier: string }) {
  const { t, n: fa, money } = useLang()
  const { data, isLoading, error } = useQuery<BriefData>({
    queryKey: ['negotiation-brief', supplier],
    queryFn: () => inventoryEnginesApi.negotiationBrief(supplier).then(r => r.data),
  })
  if (isLoading) return <p className="text-[11px] text-slate-400">{t('Preparing…', 'در حال آماده‌سازی…')}</p>
  if (error) return <p className="text-[11px] text-red-400">{apiErrorText(error)}</p>
  if (!data) return null

  return (
    <div className="mt-2 border-t border-slate-700 pt-2 space-y-3 text-[11px]">
      <p className="text-slate-400">{data.leverage.explanation}</p>

      {data.asks.length > 0 && (
        <div className="space-y-1">
          <p className="text-slate-300 font-semibold">{t('Ask for', 'درخواست کنید')}</p>
          {data.asks.map((a, i) => (
            <div key={i} className="pl-2 border-s-2 border-emerald-600/40 ps-2">
              <p className="text-emerald-200">
                {fa(i + 1)}. {a.ask}
                {a.worth !== null
                  ? <span className="text-slate-400"> — {money(a.worth)}</span>
                  : <span className="text-amber-300"> — {t('cannot be priced', 'قابل قیمت‌گذاری نیست')}</span>}
              </p>
              <p className="text-slate-500">{a.explanation}</p>
            </div>))}
        </div>)}

      {data.gaps.length > 0 && (
        <div className="overflow-x-auto">
          <table className="w-full">
            <thead className="text-slate-500 text-[10px]">
              <tr className="border-b border-slate-700">
                <th className="text-start font-normal py-1">{t('Item', 'قلم')}</th>
                <th className="text-start font-normal">{t('They charge', 'قیمت ایشان')}</th>
                <th className="text-start font-normal">{t('Already paying', 'قیمت فعلی دیگری')}</th>
                <th className="text-start font-normal">{t('Cheaper from', 'ارزان‌تر از')}</th>
                <th className="text-start font-normal">{t('A year', 'سالانه')}</th>
              </tr>
            </thead>
            <tbody>
              {data.gaps.map(g => (
                <tr key={g.ndc11} className="border-b border-slate-800/60">
                  <td className="py-1 font-mono text-slate-300">{g.ndc11}</td>
                  <td className="tabular-nums">{money(g.our_cost)}</td>
                  <td className="tabular-nums text-emerald-300">{money(g.best_cost)}</td>
                  <td className="text-slate-400">{g.best_supplier}</td>
                  <td className="tabular-nums font-semibold">{money(g.annual_value)}</td>
                </tr>))}
            </tbody>
          </table>
        </div>)}

      <div className="space-y-1">
        <p className="text-slate-300 font-semibold">{t('Cheap to concede', 'امتیازهای کم‌هزینه')}</p>
        {data.concessions.map((c, i) => (
          <p key={i} className="text-slate-400 ps-2">
            • {c.ask}
            {c.worth === null && <span className="text-amber-300"> ({t('unpriced', 'بی‌قیمت')})</span>}
            <span className="text-slate-500"> — {c.explanation}</span>
          </p>))}
      </div>

      <div className="space-y-1 rounded border border-amber-600/40 bg-amber-500/5 px-2 py-1.5">
        <p className="text-amber-300 font-semibold">
          {t('What this brief does NOT know', 'آنچه این خلاصه نمی‌داند')}
        </p>
        {data.cannot_say.map((c, i) => (
          <p key={i} className="text-amber-200/80">• {c}</p>))}
      </div>
    </div>
  )
}

/* ────────────────────────────────────────────────────────────────────────
   E13 — what is running out, grouped by whose problem it is

   The grouping is the whole point, and it is why this is not one sorted list.
   "Every supplier is out of it" and "this one supplier is rationing something
   another has in stock" need opposite responses — buy cover, or move the order —
   and the version this replaces recommended buying cover for both. Cover bought
   against a problem a phone call solves is inventory that expires on the shelf.
   ──────────────────────────────────────────────────────────────────────── */

interface Signal {
  ndc11: string; drug_name: string | null; verdict: string; action: string
  severity: string; fill_rate: number | null; basis: string
  settled_lines: number; short_suppliers: string[]; filling_suppliers: string[]
  creep: number | null; on_hand: number; days_of_cover: number | null
  demand_basis: string; lead_days: number; lead_basis: string
  horizon_days: number; suggested_buffer: number | null
  explanation: string; concerns: string[]
}
interface ShortageReport {
  signals: Signal[]; market_shortages: number; supplier_shortages: number
  unknown: number; explanation: string; as_of: string
}

const VERDICT_GROUPS: { verdict: string; title: Pair; note: Pair; cls: string }[] = [
  { verdict: 'market_shortage', title: ['The market is out', 'بازار موجود ندارد'],
    note: ['No supplier is filling these. Cover, substitute, or warn the prescribers.',
           'هیچ تأمین‌کننده‌ای این‌ها را کامل نمی‌فرستد. ذخیره کنید، جانشین بگذارید، یا پزشکان را مطلع کنید.'],
    cls: 'border-rose-600/50' },
  { verdict: 'supplier_shortage', title: ['One supplier is rationing', 'یک تأمین‌کننده جیره‌بندی می‌کند'],
    note: ['Available from someone the pharmacy already buys from — move the order rather than buy cover.',
           'از تأمین‌کننده‌ای که هم‌اکنون خرید می‌کنید موجود است — سفارش را جابه‌جا کنید، ذخیره نخرید.'],
    cls: 'border-amber-600/50' },
  { verdict: 'thin_cover', title: ['Arriving fine, not enough on the shelf', 'به‌درستی می‌رسد، اما موجودی کم است'],
    note: ['An ordinary reorder, not a shortage.', 'یک سفارش عادی است، نه کمبود.'],
    cls: 'border-sky-600/50' },
  { verdict: 'watch', title: ['Orders being quietly trimmed', 'سفارش‌ها بی‌صدا کم می‌شوند'],
    note: ['Filled on average, but each order a little short. A trailing average hides this.',
           'به‌طور میانگین تأمین می‌شود، اما هر سفارش کمی کمتر. میانگین متحرک این را پنهان می‌کند.'],
    cls: 'border-slate-600' },
]

function ShortageTab() {
  const { t, tp, n: fa } = useLang()
  const { data, isLoading, error } = useQuery<ShortageReport>({
    queryKey: ['inv-shortages'],
    queryFn: () => inventoryEnginesApi.shortages().then(r => r.data),
  })
  const pct = (v: number | null) => v === null ? '—' : `${fa(Math.round(v * 100))}٪`

  return (
    <div className="space-y-4">
      {isLoading && <p className="text-sm text-slate-400">{t('Calculating…', 'در حال محاسبه…')}</p>}
      {error && <p className="text-sm text-red-400">{apiErrorText(error)}</p>}

      {data && (<>
        <div className="bg-slate-800/50 border border-slate-700 rounded-lg p-4
                        flex flex-wrap items-center gap-x-8 gap-y-3">
          <Metric label={t('The market is out', 'بازار موجود ندارد')} value={fa(data.market_shortages)} />
          <Metric label={t('One supplier only', 'فقط یک تأمین‌کننده')} value={fa(data.supplier_shortages)} />
          <Metric label={t('Not yet judgeable', 'هنوز قابل داوری نیست')} value={fa(data.unknown)} />
          <p className="text-[11px] text-slate-500 max-w-lg">{data.explanation}</p>
        </div>

        {VERDICT_GROUPS.map(g => {
          const rows = data.signals.filter(s => s.verdict === g.verdict)
          if (!rows.length) return null
          return (
            <div key={g.verdict} className={`bg-slate-800/50 border rounded-lg p-4 space-y-2 ${g.cls}`}>
              <p className="text-sm font-semibold">{tp(g.title)}
                <span className="text-slate-500 font-normal"> · {fa(rows.length)}</span></p>
              <p className="text-[11px] text-slate-500">{tp(g.note)}</p>
              {rows.map(s => (
                <div key={s.ndc11} className="border-t border-slate-700/60 pt-2 space-y-1">
                  <div className="flex flex-wrap items-baseline gap-x-5 gap-y-1">
                    <span className="font-mono text-[12px] text-slate-300">{s.ndc11}</span>
                    {s.drug_name && <span className="text-[12px]">{s.drug_name}</span>}
                    <Metric label={t('Filled', 'تأمین‌شده')} value={pct(s.fill_rate)} />
                    <Metric label={t('Days of cover', 'روز پوشش')}
                            value={s.days_of_cover === null ? '—' : fa(s.days_of_cover)} />
                    <Metric label={t('Horizon', 'افق')} value={fa(s.horizon_days)} />
                    {s.suggested_buffer !== null && (
                      <Metric label={t('Buy', 'خرید')} value={fa(s.suggested_buffer)} />)}
                    <BasisChip basis={s.lead_basis} />
                  </div>
                  {s.short_suppliers.length > 0 && (
                    <p className="text-[11px]">
                      <span className="text-rose-300">{t('Short from', 'کسری از')}: {s.short_suppliers.join(', ')}</span>
                      {s.filling_suppliers.length > 0 && (
                        <span className="text-emerald-300">
                          {' · '}{t('filled by', 'تأمین‌شده توسط')}: {s.filling_suppliers.join(', ')}</span>)}
                    </p>)}
                  <p className="text-[11px] text-slate-400">{s.explanation}</p>
                  {s.concerns.map((c, i) => (
                    <p key={i} className="text-[11px] text-amber-300/90">• {c}</p>))}
                </div>))}
            </div>)
        })}

        {data.unknown > 0 && (
          <p className="text-[11px] text-slate-500">
            {t(`${fa(data.unknown)} item(s) have too few settled order lines to judge. They are counted, not scored — a risk number from two deliveries would be a guess with a decimal point on it.`,
               `${fa(data.unknown)} قلم ردیف سفارش تسویه‌شدهٔ کافی برای داوری ندارند. شمرده می‌شوند اما امتیاز نمی‌گیرند — عددِ ریسک از دو تحویل، یک حدس با ممیز است.`)}
          </p>)}
      </>)}
    </div>
  )
}

/* ────────────────────────────────────────────────────────────────────────
   E10 — the morning round

   The skipped list is shown as prominently as the round itself. An item left
   off because nobody has dispensed it, or because the depot is empty, is
   information the person walking the floor needs — and "not on the list" reads
   as "not needed" unless the omission is spelled out.
   ──────────────────────────────────────────────────────────────────────── */

interface PickLine {
  ndc11: string; drug_name: string | null; shelf_label: string | null
  on_shelf: number; target: number | null; shortfall: number; pull: number
  lots: { lot_number: string; expiry_date: string | null; units: number }[]
  demand_class: string; basis: string; depot_short: boolean; explanation: string
}
interface PickData {
  lines: PickLine[]
  skipped: { ndc11: string; reason: string; explanation: string }[]
  units: number; depot_shortfalls: number; explanation: string
  concerns: string[]; cover_days: number
}

const SKIP_REASON: Record<string, Pair> = {
  no_measured_demand: ['No measured demand', 'مصرفی اندازه‌گیری نشده'],
  storage_mismatch: ['Wrong storage condition', 'شرایط نگهداری نادرست'],
  shelf_full: ['Shelf is full', 'قفسه پر است'],
  depot_empty: ['Nothing in the depot', 'در انبار موجود نیست'],
}

function PickTab() {
  const { t, tp, n: fa } = useLang()
  const [coverDays, setCoverDays] = useState(1)
  const { data, isLoading, error } = useQuery<PickData>({
    queryKey: ['inv-pick', coverDays],
    queryFn: () => inventoryEnginesApi.pickList(coverDays).then(r => r.data),
  })

  return (
    <div className="space-y-4">
      <div className="bg-slate-800/50 border border-slate-700 rounded-lg p-4
                      flex flex-wrap items-center gap-x-8 gap-y-3">
        <label className="flex items-center gap-2 text-[12px]">
          <span className="text-slate-400">{t('Cover until the next round (days)', 'پوشش تا نوبت بعد (روز)')}</span>
          <input type="number" min={1} max={14} value={coverDays}
            onChange={e => setCoverDays(Math.min(14, Math.max(1, Number(e.target.value) || 1)))}
            className="w-16 bg-slate-900 border border-slate-700 rounded px-2 py-1
                       tabular-nums focus:outline-none focus:border-sky-500" />
        </label>
        {data && <>
          <Metric label={t('Items', 'اقلام')} value={fa(data.lines.length)} />
          <Metric label={t('Units to carry', 'واحد برای حمل')} value={fa(data.units)} />
          <Metric label={t('Depot cannot cover', 'انبار پوشش نمی‌دهد')} value={fa(data.depot_shortfalls)} />
        </>}
      </div>

      {isLoading && <p className="text-sm text-slate-400">{t('Calculating…', 'در حال محاسبه…')}</p>}
      {error && <p className="text-sm text-red-400">{apiErrorText(error)}</p>}

      {data && (<>
        <p className="text-[11px] text-slate-500">{data.explanation}</p>
        {data.concerns.map((c, i) => (
          <p key={i} className="text-[11px] text-amber-300/90">• {c}</p>))}

        {data.lines.length > 0 && (
          <div className="bg-slate-800/50 border border-slate-700 rounded-lg p-4">
            <div className="overflow-x-auto">
              <table className="w-full text-[12px]">
                <thead className="text-slate-500 text-[11px]">
                  <tr className="border-b border-slate-700">
                    <th className="text-start font-normal py-1.5">{t('Shelf', 'قفسه')}</th>
                    <th className="text-start font-normal">{t('Item', 'قلم')}</th>
                    <th className="text-start font-normal">{t('On shelf', 'روی قفسه')}</th>
                    <th className="text-start font-normal">{t('Target', 'هدف')}</th>
                    <th className="text-start font-normal">{t('Bring', 'بیاورید')}</th>
                    <th className="text-start font-normal">{t('From batch', 'از بچ')}</th>
                    <th className="text-start font-normal">{t('Shape', 'الگو')}</th>
                  </tr>
                </thead>
                <tbody>
                  {data.lines.map(l => (
                    <tr key={l.ndc11} className="border-b border-slate-800/60">
                      <td className="py-1.5 font-mono text-slate-400">{l.shelf_label ?? '—'}</td>
                      <td>
                        <span className="font-mono text-slate-300">{l.ndc11}</span>
                        {l.drug_name && <span className="text-slate-400 ms-2">{l.drug_name}</span>}
                      </td>
                      <td className="tabular-nums text-slate-400">{fa(l.on_shelf)}</td>
                      <td className="tabular-nums text-slate-400">
                        {l.target === null ? '—' : fa(l.target)}</td>
                      <td className="tabular-nums font-semibold">
                        {fa(l.pull)}
                        {l.depot_short && (
                          <span className="text-amber-300 ms-1" title={l.explanation}>!</span>)}
                      </td>
                      <td className="text-slate-400 font-mono text-[11px]">
                        {l.lots.map(x => `${x.lot_number} (${fa(x.units)})`).join(', ')}</td>
                      <td><ClassChip cls={l.demand_class} /></td>
                    </tr>))}
                </tbody>
              </table>
            </div>
          </div>)}

        {data.skipped.length > 0 && (
          <div className="bg-slate-800/50 border border-slate-700 rounded-lg p-4 space-y-1">
            <p className="text-[11px] text-slate-400 font-semibold">
              {t('Deliberately not on the round', 'عمداً در این نوبت نیست')}
              <span className="text-slate-500 font-normal"> · {fa(data.skipped.length)}</span>
            </p>
            <p className="text-[11px] text-slate-500">
              {t('“Not on the list” reads as “not needed” unless the reason is given.',
                 '«در فهرست نیست» یعنی «لازم نیست»، مگر آنکه دلیلش گفته شود.')}
            </p>
            {data.skipped.slice(0, 25).map((s, i) => (
              <p key={i} className="text-[11px] text-slate-400">
                <span className="font-mono">{s.ndc11}</span>
                <span className="text-slate-500"> — {SKIP_REASON[s.reason] ? tp(SKIP_REASON[s.reason]) : s.reason}</span>
                <span className="text-slate-600"> · {s.explanation}</span>
              </p>))}
          </div>)}
      </>)}
    </div>
  )
}

/* ────────────────────────────────────────────────────────────────────────
   E7 — seasonality, by Jalali month

   The verdict is shown before any number. "Insufficient cycles" is the most
   common answer for a long time and it is not a failure state — it is the
   engine declining to buy for a season it cannot yet see.
   ──────────────────────────────────────────────────────────────────────── */

interface SeasonItem {
  ndc11: string; drug_name: string | null; verdict: string; cycles: number
  months_observed: number; total_units: number; strength: number | null
  peak: { month: number; name: string; index: number | null } | null
  trough: { month: number; name: string; index: number | null } | null
  months: { month: number; name: string; observations: number
            index: number | null; basis: string }[]
  explanation: string; concerns: string[]
}
interface SeasonData {
  items: SeasonItem[]; seasonal: number; insufficient_cycles: number
  no_pattern: number; explanation: string; as_of: string
}

const SEASON_VERDICT: Record<string, { label: Pair; cls: string }> = {
  seasonal: { label: ['Seasonal', 'فصلی'], cls: 'bg-emerald-500/15 text-emerald-300 border-emerald-500/40' },
  no_detectable_seasonality: { label: ['No pattern', 'بدون الگو'], cls: 'bg-slate-600/20 text-slate-400 border-slate-600' },
  insufficient_cycles: { label: ['Not enough years', 'سال‌های کافی نیست'], cls: 'bg-amber-500/15 text-amber-300 border-amber-500/40' },
  no_history: { label: ['No history', 'سابقه‌ای نیست'], cls: 'bg-slate-600/20 text-slate-400 border-slate-600' },
}

function SeasonTab() {
  const { t, tp, n: fa } = useLang()
  const { data, isLoading, error } = useQuery<SeasonData>({
    queryKey: ['inv-season'],
    queryFn: () => inventoryEnginesApi.seasonality().then(r => r.data),
  })

  return (
    <div className="space-y-4">
      <p className="text-[11px] text-slate-500">
        {t('Months are Jalali. Nowruz is 1 Farvardin every year and drifts across 20–21 March, so a Gregorian bucket splits the new-year peak in two and halves it.',
           'ماه‌ها شمسی است. نوروز هر سال ۱ فروردین است و در تقویم میلادی میان ۲۰ و ۲۱ مارس جابه‌جا می‌شود؛ بنابراین دسته‌بندی میلادی اوج نوروز را دو نیم می‌کند.')}
      </p>

      {isLoading && <p className="text-sm text-slate-400">{t('Calculating…', 'در حال محاسبه…')}</p>}
      {error && <p className="text-sm text-red-400">{apiErrorText(error)}</p>}

      {data && (<>
        <div className="bg-slate-800/50 border border-slate-700 rounded-lg p-4
                        flex flex-wrap items-center gap-x-8 gap-y-3">
          <Metric label={t('Seasonal', 'فصلی')} value={fa(data.seasonal)} />
          <Metric label={t('No pattern', 'بدون الگو')} value={fa(data.no_pattern)} />
          <Metric label={t('Not enough years', 'سال‌های کافی نیست')} value={fa(data.insufficient_cycles)} />
          <p className="text-[11px] text-slate-500 max-w-lg">{data.explanation}</p>
        </div>

        <div className="space-y-2">
          {data.items.slice(0, 40).map(s => {
            const v = SEASON_VERDICT[s.verdict] ?? SEASON_VERDICT.no_history
            return (
              <div key={s.ndc11} className="bg-slate-800/50 border border-slate-700 rounded-lg p-3 space-y-1">
                <div className="flex flex-wrap items-baseline gap-x-5 gap-y-1">
                  <span className="font-mono text-[12px] text-slate-300">{s.ndc11}</span>
                  {s.drug_name && <span className="text-[12px]">{s.drug_name}</span>}
                  <span className={`text-[10px] px-1.5 py-0.5 rounded border ${v.cls}`}>{tp(v.label)}</span>
                  {s.strength !== null && (
                    <Metric label={t('Strength', 'شدت')} value={`${fa(Math.round(s.strength * 100))}٪`} />)}
                  <Metric label={t('Cycles', 'دوره‌ها')} value={fa(s.cycles)} />
                </div>
                {s.verdict === 'seasonal' && (
                  <div className="flex flex-wrap gap-1 pt-1">
                    {s.months.map(m => (
                      <span key={m.month}
                        title={m.index === null
                          ? tp(['Seen too few times to quote', 'دفعات مشاهده برای اعلام کافی نیست'])
                          : `${m.name}: ${m.index}\u00d7`}
                        className={`text-[10px] px-1.5 py-0.5 rounded border tabular-nums ${
                          m.index === null ? 'bg-slate-800 border-slate-700 text-slate-600'
                          : m.index >= 1.2 ? 'bg-rose-500/15 border-rose-500/40 text-rose-300'
                          : m.index <= 0.8 ? 'bg-sky-500/15 border-sky-500/40 text-sky-300'
                          : 'bg-slate-700/40 border-slate-600 text-slate-400'}`}>
                        {m.name.slice(0, 3)} {m.index === null ? '—' : fa(m.index)}
                      </span>))}
                  </div>)}
                <p className="text-[11px] text-slate-400">{s.explanation}</p>
                {s.concerns.map((c, i) => (
                  <p key={i} className="text-[11px] text-slate-500">• {c}</p>))}
              </div>)
          })}
        </div>
      </>)}
    </div>
  )
}

/* ── shared bits ─────────────────────────────────────────────────────── */

function BasisChip({ basis }: { basis: string }) {
  const { tp } = useLang()
  const b = BASIS[basis] ?? BASIS.no_history
  return (
    <span title={tp(b.title)}
      className={`text-[10px] px-1.5 py-0.5 rounded border whitespace-nowrap ${b.cls}`}>
      {tp(b.label)}
    </span>)
}

function Metric({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <div className="text-[10px] text-slate-500">{label}</div>
      <div className="text-lg font-bold tabular-nums">{value}</div>
    </div>
  )
}
