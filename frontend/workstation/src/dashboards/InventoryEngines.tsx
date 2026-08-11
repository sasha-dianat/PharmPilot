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

const fa = (n: number) => new Intl.NumberFormat('fa-IR').format(n)
const faMoney = (n: number) =>
  new Intl.NumberFormat('fa-IR', { maximumFractionDigits: 0 }).format(n)

type Tab = 'advice' | 'count' | 'demand' | 'value'

const TABS: { id: Tab; label: string; hint: string }[] = [
  { id: 'advice', label: 'توصیه‌ها', hint: 'پیشنهادهای موتورها و تصمیم شما' },
  { id: 'count', label: 'شمارش دوره‌ای', hint: 'کجا وقت شمارش صرف شود' },
  { id: 'demand', label: 'سیگنال تقاضا', hint: 'نرخ مصرف اندازه‌گیری‌شده' },
  { id: 'value', label: 'ارزش و ضایعات', hint: 'ارزش موجودی و بهای زیان' },
]

/** How a number was arrived at. Shown, never hidden. */
const BASIS: Record<string, { label: string; cls: string; title: string }> = {
  observed: {
    label: 'اندازه‌گیری‌شده', cls: 'bg-emerald-500/15 text-emerald-300 border-emerald-500/40',
    title: 'از سوابق واقعی این داروخانه محاسبه شده است',
  },
  sparse: {
    label: 'داده کم', cls: 'bg-sky-500/15 text-sky-300 border-sky-500/40',
    title: 'داده‌های کافی برای یک نرخ قابل اتکا وجود ندارد؛ موقتی است',
  },
  no_history: {
    label: 'سابقه‌ای نیست', cls: 'bg-slate-600/20 text-slate-400 border-slate-600',
    title: 'هیچ مصرفی ثبت نشده؛ عددی حدس زده نمی‌شود',
  },
  declared_default: {
    label: 'فرض اعلام‌شده', cls: 'bg-amber-500/15 text-amber-300 border-amber-500/40',
    title: 'اندازه‌گیری نشده است؛ این یک فرض صریح است، نه مشاهده',
  },
}

const SEV: Record<string, string> = {
  critical: 'bg-rose-500/15 text-rose-300 border-rose-500/40',
  high: 'bg-amber-500/15 text-amber-300 border-amber-500/40',
  medium: 'bg-sky-500/15 text-sky-300 border-sky-500/40',
  info: 'bg-slate-600/20 text-slate-300 border-slate-600',
}

const VERDICT: Record<string, { label: string; cls: string; note: string }> = {
  trusted: { label: 'قابل اتکا', cls: 'text-emerald-300', note: 'بیشتر پیشنهادها پذیرفته می‌شود' },
  mixed: { label: 'مختلط', cls: 'text-sky-300', note: '' },
  noisy: { label: 'پرنویز', cls: 'text-amber-300', note: 'آستانه بیش از ارزش، کار تولید می‌کند' },
  ignored: {
    label: 'نادیده گرفته می‌شود', cls: 'text-rose-300',
    note: 'زیاد تولید می‌کند و تقریباً هیچ تصمیمی روی آن گرفته نمی‌شود',
  },
  unmeasured: {
    label: 'هنوز سنجش‌پذیر نیست', cls: 'text-slate-400',
    note: 'تصمیم‌های کافی برای اظهار نظر ثبت نشده است',
  },
}

export default function InventoryEngines() {
  const [tab, setTab] = useState<Tab>('advice')
  const [msg, setMsg] = useState<{ kind: 'ok' | 'err'; text: string } | null>(null)

  return (
    <div className="p-4 space-y-4 text-slate-100" dir="rtl">
      <div className="flex flex-wrap items-baseline gap-3">
        <h2 className="text-lg font-bold">موتورهای موجودی</h2>
        <span className="text-[11px] text-slate-500">
          هر عدد با منشأ خود نمایش داده می‌شود؛ نرخی که اندازه‌گیری نشده باشد، حدس زده نمی‌شود.
        </span>
      </div>

      <div className="flex flex-wrap gap-1.5 border-b border-slate-700 pb-2">
        {TABS.map(t => (
          <button key={t.id} onClick={() => { setTab(t.id); setMsg(null) }} title={t.hint}
            className={`px-3 py-1.5 rounded-t text-[13px] border-b-2 -mb-[9px] transition-colors ${
              tab === t.id
                ? 'border-sky-400 text-sky-300 bg-slate-800/60'
                : 'border-transparent text-slate-400 hover:text-slate-200'}`}>
            {t.label}
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
          ? 'پذیرفته شد. اجرای آن همچنان از مسیر تأیید و دفتر موجودی انجام می‌شود.'
          : 'رد شد؛ دلیل شما ثبت گردید و مبنای تنظیم آستانه خواهد بود.',
      })
      setRejecting(null); setNote('')
      qc.invalidateQueries({ queryKey: ['inv-recs'] })
      qc.invalidateQueries({ queryKey: ['inv-rec-score'] })
    } catch (e: unknown) {
      onMsg({ kind: 'err', text: apiErrorText(e, 'ثبت تصمیم ناموفق بود.') })
    } finally { setBusy(false) }
  }

  return (
    <div className="space-y-4">
      {/* The scoreboard sits above the queue on purpose: an operator should see
          that a detector is being ignored before working through more of it. */}
      {board && board.scores.length > 0 && (
        <div className="bg-slate-800/40 border border-slate-700 rounded-lg px-4 py-3">
          <p className="text-[11px] text-slate-500 pb-2">
            کارنامهٔ موتورها — «پذیرش» یعنی موافقت کارشناس، نه درستی نتیجه.
          </p>
          <div className="flex flex-wrap gap-3">
            {board.scores.map(s => {
              const v = VERDICT[s.verdict] ?? VERDICT.unmeasured
              return (
                <div key={s.kind} title={s.explanation}
                  className="bg-slate-900/60 border border-slate-700 rounded px-3 py-2 min-w-[190px]">
                  <div className="flex items-baseline justify-between gap-3">
                    <span className="text-[12px] text-slate-300">{s.kind}</span>
                    <span className={`text-[12px] font-semibold ${v.cls}`}>{v.label}</span>
                  </div>
                  <div className="text-[11px] text-slate-500 tabular-nums pt-1">
                    {fa(s.produced)} پیشنهاد · {fa(s.decided)} تصمیم
                    {s.acceptance_rate !== null &&
                      <> · پذیرش {fa(Math.round(s.acceptance_rate * 100))}٪</>}
                  </div>
                  {v.note && <div className="text-[10px] text-slate-600 pt-0.5">{v.note}</div>}
                </div>)
            })}
          </div>
        </div>)}

      <div className="bg-slate-800/50 border border-slate-700 rounded-lg p-4 space-y-2">
        <div className="flex items-center gap-2">
          <span className="font-semibold text-sm">پیشنهادهای باز</span>
          <span className="text-[11px] text-slate-500">
            رد کردن نیازمند دلیل است — دلیل شما تنها چیزی است که بعداً قابل بازسازی نیست.
          </span>
        </div>
        {isLoading && <p className="text-sm text-slate-400">در حال بارگذاری…</p>}
        {error && <p className="text-sm text-red-400">{apiErrorText(error)}</p>}
        {data && data.count === 0 && (
          <p className="text-[12px] text-slate-500">پیشنهاد بازی وجود ندارد.</p>)}

        {data?.recommendations.map(r => (
          <div key={r.id} className="border-t border-slate-700/60 pt-2 space-y-1.5">
            <div className="flex flex-wrap items-center gap-2 text-[12px]">
              <span className={`text-[10px] px-1.5 py-0.5 rounded border ${
                SEV[r.severity ?? 'info'] ?? SEV.info}`}>{r.kind}</span>
              {r.ndc11 && <span className="font-mono text-slate-400">{r.ndc11}</span>}
              <span className="text-slate-300">{r.explanation}</span>
              {r.confidence !== null && (
                <span className="text-[11px] text-slate-500 tabular-nums">
                  اطمینان {fa(Math.round(r.confidence * 100))}٪
                </span>)}
              <span className="text-[10px] text-slate-600">{r.produced_by}</span>

              <div className="mr-auto flex gap-2">
                <button onClick={() => decide(r.id, true)} disabled={busy}
                  className="px-3 py-1 bg-emerald-600 hover:bg-emerald-500 rounded disabled:opacity-50">
                  پذیرش
                </button>
                <button onClick={() => { setRejecting(rejecting === r.id ? null : r.id); setNote('') }}
                  disabled={busy}
                  className="px-3 py-1 bg-slate-700 hover:bg-slate-600 rounded disabled:opacity-50">
                  رد
                </button>
              </div>
            </div>

            {rejecting === r.id && (
              <div className="flex flex-wrap items-center gap-2 pb-1">
                <input autoFocus value={note} onChange={e => setNote(e.target.value)}
                  placeholder="چرا این پیشنهاد درست نیست؟"
                  className="flex-1 min-w-[240px] bg-slate-900 border border-slate-700 rounded
                             px-2 py-1 text-[12px] focus:outline-none focus:border-sky-500" />
                <button disabled={busy || !note.trim()}
                  onClick={() => decide(r.id, false, note.trim())}
                  className="px-3 py-1 bg-rose-600 hover:bg-rose-500 rounded text-[12px]
                             disabled:opacity-40">
                  ثبت رد
                </button>
                {!note.trim() && (
                  <span className="text-[10px] text-slate-500">دلیل الزامی است</span>)}
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

const RISK_FA: Record<string, string> = {
  controlled: 'تحت کنترل',
  recent_variance: 'مغایرت اخیر',
  expiring: 'نزدیک انقضا',
  unpredictable: 'مصرف نامنظم',
  high_value_lot: 'بچ پرارزش',
}

function CountTab() {
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
          <span className="text-slate-400">ظرفیت هر نوبت</span>
          <input type="number" min={1} max={500} value={capacity}
            onChange={e => setCapacity(Math.max(1, Number(e.target.value) || 1))}
            className="w-20 bg-slate-900 border border-slate-700 rounded px-2 py-1
                       tabular-nums focus:outline-none focus:border-sky-500" />
        </label>
        {data && <>
          <Metric label="اقلام طبقه‌بندی‌شده" value={fa(data.items_classified)} />
          <Metric label="اکنون سررسید" value={fa(data.due_now)} />
          <Metric label="در این نوبت" value={fa(data.session.counted)} />
          <Metric label="موکول‌شده" value={fa(data.session.deferred)} />
        </>}
      </div>

      {isLoading && <p className="text-sm text-slate-400">در حال محاسبه…</p>}
      {error && <p className="text-sm text-red-400">{apiErrorText(error)}</p>}

      {data && (
        <>
          {/* The comparison is reported, not claimed. A risk-ranked schedule can
              cost more than the flat sweep it replaces, and that must be visible. */}
          <div className="bg-slate-800/40 border border-slate-700 rounded-lg px-4 py-3">
            <p className="text-[11px] text-slate-500 pb-2">
              در مقایسه با شمارش یکنواخت ۹۰ روزه
            </p>
            <div className="flex flex-wrap items-center gap-x-8 gap-y-2">
              <Metric label="خطوط شمارش (این روش)" value={fa(data.effort.ranked_lines)} />
              <Metric label="خطوط شمارش (یکنواخت)" value={fa(data.effort.flat_lines)} />
              <div>
                <div className="text-[10px] text-slate-500">تغییر</div>
                <div className={`text-lg font-bold tabular-nums ${
                  data.effort.pct_change <= 0 ? 'text-emerald-300' : 'text-amber-300'}`}>
                  {data.effort.pct_change > 0 ? '+' : ''}{fa(data.effort.pct_change)}٪
                </div>
              </div>
              <div>
                <div className="text-[10px] text-slate-500">توجه بیشتر روی اقلام کلاس A</div>
                <div className="text-lg font-bold tabular-nums text-sky-300">
                  {data.effort.a_class_coverage_gain > 0 ? '+' : ''}
                  {fa(data.effort.a_class_coverage_gain)}
                </div>
              </div>
            </div>
            {data.effort.pct_change > 0 && (
              <p className="text-[11px] text-amber-300/80 pt-2">
                این برنامه خطوط شمارش بیشتری از روش یکنواخت می‌خواهد. صرفه‌جویی از دنبالهٔ
                اقلام کم‌ارزش می‌آید و در فهرست‌های کوچک چنین دنباله‌ای وجود ندارد؛ آنچه
                اکنون رخ می‌دهد جابه‌جایی توجه به سمت اقلام پرارزش است.
              </p>)}
          </div>

          <div className="bg-slate-800/50 border border-slate-700 rounded-lg p-4 space-y-2">
            <div className="flex flex-wrap items-center gap-2">
              <span className="font-semibold text-sm">برگهٔ شمارش بعدی</span>
              <span className="text-[11px] text-slate-500">{data.session.coverage_note}</span>
            </div>
            {data.session.lines.length === 0 && (
              <p className="text-[12px] text-slate-500">موردی سررسید نشده است.</p>)}
            {data.session.lines.map(l => (
              <div key={l.ndc11}
                className="flex flex-wrap items-center gap-3 border-t border-slate-700/60 pt-2 text-[12px]">
                <span className="font-mono text-slate-300">{l.ndc11}</span>
                <span className="text-[10px] px-1.5 py-0.5 rounded bg-slate-700/50
                                 border border-slate-600 text-slate-300">{l.class}</span>
                <span className="text-slate-500">هر {fa(l.interval_days)} روز</span>
                <span className="text-slate-400">{l.reason}</span>
                {l.risks.map(r => (
                  <span key={r} className="text-[10px] px-1.5 py-0.5 rounded
                                           bg-amber-500/15 text-amber-300 border border-amber-500/40">
                    {RISK_FA[r] ?? r}
                  </span>))}
              </div>))}
            {data.session.worst_deferred && (
              <p className="text-[11px] text-slate-500 pt-2 border-t border-slate-700/60">
                مهم‌ترین مورد موکول‌شده: <span className="font-mono">
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
  signals: {
    reorder_point: number | null; safety_stock: number | null
    lead_time_basis: string; lead_time_days: number; explanation: string
  }
}
interface Refresh {
  applied: boolean; window_days: number; as_of: string
  lead_time: { days: number; basis: string; samples: number; explanation: string }
  rows: RefreshRow[]; written: number
  summary: {
    items: number; changed: number; by_basis: Record<string, number>
    prior_signal_verdict: Record<string, number>; reorder_points_set: number
  }
}

const VERDICT_FA: Record<string, { label: string; cls: string }> = {
  contradicted: { label: 'با سوابق در تضاد', cls: 'text-rose-300' },
  understated: { label: 'کمتر از واقع', cls: 'text-amber-300' },
  overstated: { label: 'بیش از واقع', cls: 'text-sky-300' },
  agrees: { label: 'هم‌خوان', cls: 'text-emerald-300' },
  indeterminate: { label: '—', cls: 'text-slate-500' },
}

function DemandTab({ onMsg }: { onMsg: (m: { kind: 'ok' | 'err'; text: string }) => void }) {
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
      onMsg({ kind: 'ok', text: `نرخ مصرف برای ${fa(r.data.written)} قلم بازنویسی شد.` })
      qc.invalidateQueries({ queryKey: ['inv-demand'] })
      qc.invalidateQueries({ queryKey: ['inv-reconciliation'] })
    } catch (e: unknown) {
      onMsg({ kind: 'err', text: apiErrorText(e, 'بازمحاسبه ناموفق بود.') })
    } finally { setBusy(false) }
  }

  return (
    <div className="space-y-4">
      <div className="bg-slate-800/50 border border-slate-700 rounded-lg p-4
                      flex flex-wrap items-center gap-x-8 gap-y-3">
        <label className="flex items-center gap-2 text-[12px]">
          <span className="text-slate-400">پنجرهٔ مشاهده (روز)</span>
          <input type="number" min={7} max={365} value={windowDays}
            onChange={e => setWindowDays(Math.min(365, Math.max(7, Number(e.target.value) || 7)))}
            className="w-20 bg-slate-900 border border-slate-700 rounded px-2 py-1
                       tabular-nums focus:outline-none focus:border-sky-500" />
        </label>
        {data && <>
          <Metric label="اقلام" value={fa(data.summary.items)} />
          <Metric label="تغییر می‌کند" value={fa(data.summary.changed)} />
          <Metric label="نقطهٔ سفارش قابل تعیین" value={fa(data.summary.reorder_points_set)} />
          <div>
            <div className="text-[10px] text-slate-500">زمان تدارک</div>
            <div className="flex items-center gap-1.5">
              <span className="text-lg font-bold tabular-nums">{fa(data.lead_time.days)}</span>
              <BasisChip basis={data.lead_time.basis} />
            </div>
          </div>
          <button onClick={apply} disabled={busy}
            className="mr-auto px-4 py-1.5 bg-sky-600 hover:bg-sky-500 rounded
                       text-[13px] disabled:opacity-50">
            بازمحاسبه و ثبت
          </button>
        </>}
      </div>

      {isLoading && <p className="text-sm text-slate-400">در حال محاسبه…</p>}
      {error && <p className="text-sm text-red-400">{apiErrorText(error)}</p>}

      {data && (
        <div className="bg-slate-800/50 border border-slate-700 rounded-lg p-4">
          <p className="text-[11px] text-slate-500 pb-2">
            پیش‌نمایش. ستون «وضعیت مقدار قبلی» می‌گوید عدد ذخیره‌شده در برابر سوابق تحویل
            چه وضعی داشته است — نه فقط اینکه به چه چیزی تبدیل می‌شود.
          </p>
          <div className="overflow-x-auto">
            <table className="w-full text-[12px]">
              <thead className="text-slate-500 text-[11px]">
                <tr className="border-b border-slate-700">
                  <th className="text-right font-normal py-1.5">قلم</th>
                  <th className="text-right font-normal">مقدار فعلی</th>
                  <th className="text-right font-normal">مقدار جدید</th>
                  <th className="text-right font-normal">منشأ</th>
                  <th className="text-right font-normal">مصرف مشاهده‌شده</th>
                  <th className="text-right font-normal">نقطهٔ سفارش</th>
                  <th className="text-right font-normal">وضعیت مقدار قبلی</th>
                </tr>
              </thead>
              <tbody>
                {data.rows.map(r => {
                  const v = VERDICT_FA[r.verdict] ?? VERDICT_FA.indeterminate
                  return (
                    <tr key={r.ndc11} className="border-b border-slate-800/60">
                      <td className="py-1.5 font-mono text-slate-300">{r.ndc11}</td>
                      <td className="tabular-nums text-slate-400">
                        {r.stored_adq === null ? '—' : fa(r.stored_adq)}</td>
                      <td className="tabular-nums">
                        {r.new_adq === null
                          ? <span className="text-slate-500">اندازه‌گیری نشد</span>
                          : <span className="text-slate-100 font-semibold">{fa(r.new_adq)}</span>}
                      </td>
                      <td><BasisChip basis={r.basis} /></td>
                      <td className="tabular-nums text-slate-400">
                        {fa(r.units_observed)} در {fa(r.window_days)} روز</td>
                      <td className="tabular-nums text-slate-400">
                        {r.signals?.reorder_point === null || r.signals?.reorder_point === undefined
                          ? <span className="text-slate-600" title={r.signals?.explanation}>—</span>
                          : fa(Math.round(r.signals.reorder_point))}
                      </td>
                      <td className={v.cls} title={r.explanation}>{v.label}</td>
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

const BUCKET_FA: Record<string, string> = {
  damaged: 'آسیب‌دیده', returned: 'مرجوعی', in_transit: 'در راه',
}

function ValueTab() {
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
          <span className="text-slate-400">روش</span>
          <select value={method} onChange={e => setMethod(e.target.value as 'fifo' | 'weighted')}
            className="bg-slate-900 border border-slate-700 rounded px-2 py-1
                       focus:outline-none focus:border-sky-500">
            <option value="fifo">اولین‌صادره از اولین‌وارده</option>
            <option value="weighted">میانگین موزون</option>
          </select>
        </label>
        {data && <>
          <Metric label="ارزش موجودی قابل فروش" value={faMoney(data.valuation.total)} />
          {Object.entries(data.valuation.buckets).map(([k, v]) => (
            <Metric key={k} label={BUCKET_FA[k] ?? k} value={faMoney(v)} />))}
        </>}
      </div>

      {isLoading && <p className="text-sm text-slate-400">در حال محاسبه…</p>}
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
              <span className="font-semibold text-sm">ضایعات {fa(data.shrinkage.period_days)} روز اخیر</span>
              <Metric label="از دست رفته" value={faMoney(data.shrinkage.total_lost)} />
              <Metric label="قابل بازیافت از تأمین‌کننده" value={faMoney(data.shrinkage.recoverable)} />
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
            <p className="text-[11px] text-slate-500 pb-2">پرارزش‌ترین اقلام</p>
            <div className="overflow-x-auto">
              <table className="w-full text-[12px]">
                <thead className="text-slate-500 text-[11px]">
                  <tr className="border-b border-slate-700">
                    <th className="text-right font-normal py-1.5">قلم</th>
                    <th className="text-right font-normal">موجودی</th>
                    <th className="text-right font-normal">بهای واحد</th>
                    <th className="text-right font-normal">ارزش</th>
                    <th className="text-right font-normal">بچ‌های بدون بها</th>
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
                          : <span className="text-slate-600">۰</span>}
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

/* ── shared bits ─────────────────────────────────────────────────────── */

function BasisChip({ basis }: { basis: string }) {
  const b = BASIS[basis] ?? BASIS.no_history
  return (
    <span title={b.title}
      className={`text-[10px] px-1.5 py-0.5 rounded border whitespace-nowrap ${b.cls}`}>
      {b.label}
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
