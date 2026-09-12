/**
 * Real-time Rx dispensing queue — the primary pharmacist workstation view.
 * Shows all active prescriptions color-coded by status and clinical risk.
 * Keyboard navigable: Arrow keys + Enter to select, C to claim, R to release.
 *
 * "Clinical Daylight" UI: rows lead with the short Rx token (#0196, the
 * rx_number tail) + status + patient anchor + lane colour, never the drug name
 * as the primary key. All store/keyboard/claim/grouping logic is unchanged.
 */
import { useEffect, useRef, useState } from 'react'
import { useRxQueueStore, type Prescription, type RxStatus } from '../stores/rxQueue'
import { rxApi } from '../lib/api'
import ReceptionQuotePanel from './ReceptionQuotePanel'
import { useLang } from '../lib/i18n'

const STATUS_CONFIG: Record<RxStatus, { label: string; tone: string; lane: string; priority: number }> = {
  intake:                    { label: 'Intake',        tone: 'bg-surface2 text-ink2 border-line2',          lane: 'bg-line2',    priority: 5 },
  pending_dur:               { label: 'DUR pending',   tone: 'bg-counsel-soft text-counsel border-counsel/25', lane: 'bg-counsel', priority: 4 },
  dur_hold:                  { label: 'DUR hold',      tone: 'bg-blocker-soft text-blocker border-blocker/30', lane: 'bg-blocker', priority: 1 },
  pending_verification:      { label: 'Verify',        tone: 'bg-intel-soft text-intel border-intel/25',    lane: 'bg-intel',    priority: 2 },
  verification_in_progress:  { label: 'In progress',   tone: 'bg-intel-soft text-intel border-intel/25',    lane: 'bg-intel',    priority: 3 },
  pending_adjudication:      { label: 'Adjudicating',  tone: 'bg-warning-soft text-warning border-warning/25', lane: 'bg-warning', priority: 3 },
  adjudication_rejected:     { label: 'Rejected',      tone: 'bg-blocker-soft text-blocker border-blocker/30', lane: 'bg-blocker', priority: 1 },
  pending_pa:                { label: 'PA required',   tone: 'bg-caution-soft text-caution border-caution/30', lane: 'bg-caution', priority: 2 },
  ready_to_fill:             { label: 'Ready to fill', tone: 'bg-safe-soft text-safe border-safe/25',        lane: 'bg-safe',     priority: 2 },
  filling:                   { label: 'Filling',       tone: 'bg-safe-soft text-safe border-safe/25',        lane: 'bg-safe',     priority: 3 },
  filled:                    { label: 'Filled',        tone: 'bg-safe-soft text-safe border-safe/25',        lane: 'bg-safe',     priority: 4 },
  will_call:                 { label: 'Will call',     tone: 'bg-intel-soft text-intel border-intel/25',    lane: 'bg-intel',    priority: 5 },
  dispensed:                 { label: 'Dispensed',     tone: 'bg-surface2 text-ink3 border-line2',          lane: 'bg-line2',    priority: 9 },
  cancelled:                 { label: 'Cancelled',     tone: 'bg-surface2 text-ink3 border-line2',          lane: 'bg-line2',    priority: 9 },
  on_hold:                   { label: 'On hold',       tone: 'bg-warning-soft text-warning border-warning/25', lane: 'bg-warning', priority: 6 },
}

function rxToken(rxNumber: string | undefined): string {
  const n = rxNumber ?? ''
  return n.length > 4 ? `#${n.slice(-4)}` : (n ? `#${n}` : '#----')
}


export default function RxQueue() {
  const { t } = useLang()
  const { queue, selectedRxId, selectRx, setSelectedRx } = useRxQueueStore()
  const containerRef = useRef<HTMLDivElement>(null)

  // Keyboard navigation
  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if (!queue.length) return
      const currentIdx = queue.findIndex((rx) => rx.id === selectedRxId)

      if (e.key === 'ArrowDown') {
        e.preventDefault()
        const nextIdx = Math.min(currentIdx + 1, queue.length - 1)
        selectRx(queue[nextIdx].id)
      } else if (e.key === 'ArrowUp') {
        e.preventDefault()
        const prevIdx = Math.max(currentIdx - 1, 0)
        selectRx(queue[prevIdx].id)
      }
    }

    containerRef.current?.addEventListener('keydown', handleKeyDown)
    return () => containerRef.current?.removeEventListener('keydown', handleKeyDown)
  }, [queue, selectedRxId, selectRx])

  const handleClaim = async (rxId: string) => {
    try {
      const { data } = await rxApi.claim(rxId)
      setSelectedRx(data.rx)
    } catch (err: unknown) {
      const axiosError = err as { response?: { status: number; data: { detail: string } } }
      if (axiosError.response?.status === 409) {
        alert(`Cannot claim: ${axiosError.response.data.detail}`)
      }
    }
  }

  // Reception quote modal state (the affordability-negotiation surface)
  const [quoteBasket, setQuoteBasket] = useState<{ name: string; items: { drug_name: string; quantity: number }[] } | null>(null)

  const activeRxs = queue.filter((rx) =>
    !['dispensed', 'cancelled', 'returned_to_stock'].includes(rx.status)
  )

  // ── Group prescriptions into per-patient baskets ───────────────────────────
  // A physician usually prescribes several drugs at once; the queue shows them as
  // ONE basket per patient (not one card per drug), most-urgent patient first.
  const baskets = new Map<string, { name: string; rxs: Prescription[] }>()
  for (const rx of activeRxs) {
    const name = (rx as any).patient_name || t('Patient', 'بیمار')
    const b = baskets.get(rx.patient_id) ?? { name, rxs: [] as Prescription[] }
    b.rxs.push(rx)
    baskets.set(rx.patient_id, b)
  }
  const basketPriority = (rxs: Prescription[]) =>
    Math.min(...rxs.map((r) => (STATUS_CONFIG[r.status] || STATUS_CONFIG.intake).priority))
  const orderedBaskets = [...baskets.entries()].sort(
    (a, b) => basketPriority(a[1].rxs) - basketPriority(b[1].rxs)
  )

  const openQuote = (name: string, rxs: Prescription[]) =>
    setQuoteBasket({
      name,
      items: rxs.map((r) => ({
        drug_name: `${r.drug_name}${r.drug_strength ? ' ' + r.drug_strength : ''}`,
        quantity: r.quantity_prescribed || 1,
      })),
    })

  return (
    <div ref={containerRef} className="cd-scope h-full overflow-y-auto focus:outline-none" tabIndex={-1}>
      <div className="p-3 space-y-3">
        <div className="flex items-center justify-between">
          <h2 className="cd-ui font-semibold text-ink">{t('Rx queue', 'صف نسخه‌ها')}</h2>
          <span className="cd-data text-xs text-ink3">
            {t(`${baskets.size} patients · ${activeRxs.length} items`,
               `${baskets.size} بیمار · ${activeRxs.length} اقلام`)}</span>
        </div>

        {activeRxs.length === 0 ? (
          <div className="text-center py-8 text-ink3 text-sm">{t('Queue is empty', 'صف خالی است')}</div>
        ) : (
          orderedBaskets.map(([pid, basket]) => {
            const lane = (STATUS_CONFIG[basket.rxs[0].status] || STATUS_CONFIG.intake).lane
            const alerts = basket.rxs.reduce((n, r) => n + (r.dur_alerts?.length ?? 0), 0)
            return (
              <div key={pid} className="cd-card cd-section overflow-hidden">
                {/* basket header = the patient (click → review the whole basket) */}
                <div onClick={() => selectRx(basket.rxs[0].id)}
                  className="flex items-center gap-2 px-3 py-2 border-b border-line bg-surface2/60 cursor-pointer hover:bg-surface2">
                  <span className={`w-1.5 h-1.5 rounded-full ${lane}`} />
                  <span className="cd-ui text-[13px] font-semibold text-ink truncate">{basket.name}</span>
                  <span className="cd-data text-[10px] text-ink3">{t(`${basket.rxs.length} items`, `${basket.rxs.length} قلم`)}</span>
                  {alerts > 0 && (
                    <span className="cd-data text-[10px] bg-caution-soft text-caution px-1.5 rounded">⚠ {alerts}</span>
                  )}
                  <button
                    onClick={(e) => { e.stopPropagation(); openQuote(basket.name, basket.rxs) }}
                    className="cd-ui ms-auto text-[11px] px-2 py-1 rounded-lg bg-intel-soft text-intel border border-intel/30 hover:brightness-105">
                    💳 {t('Price quote', 'استعلام قیمت')}
                  </button>
                </div>
                {/* medications in the basket */}
                <div className="divide-y divide-line">
                  {basket.rxs.map((rx) => {
                    const st = STATUS_CONFIG[rx.status] || STATUS_CONFIG.intake
                    const isSel = rx.id === selectedRxId
                    const crit = rx.dur_alerts?.filter((a) => a.severity === 'critical').length ?? 0
                    return (
                      <button
                        key={rx.id}
                        onClick={() => selectRx(rx.id)}
                        onDoubleClick={() => handleClaim(rx.id)}
                        className={`w-full text-left flex items-center gap-2 px-3 py-2 ${isSel ? 'bg-intel-soft' : 'hover:bg-surface2'}`}>
                        <span className={`cd-data text-[11px] ${isSel ? 'text-intel font-semibold' : 'text-ink3'}`}>{rxToken(rx.rx_number)}</span>
                        <span className="text-[12px] text-ink truncate flex-1">
                          {rx.drug_name} {rx.drug_strength}
                          {rx.is_controlled && <span className="cd-data ml-1 text-[9px] bg-caution-soft text-caution px-1 rounded">{rx.dea_schedule}</span>}
                        </span>
                        {crit > 0 && <span className="cd-data text-[10px] bg-blocker-soft text-blocker px-1 rounded">🚫{crit}</span>}
                        <span className={`cd-data text-[10px] px-1.5 py-0.5 rounded border ${st.tone}`}>{st.label}</span>
                      </button>
                    )
                  })}
                </div>
              </div>
            )
          })
        )}
      </div>

      {quoteBasket && (
        <ReceptionQuotePanel
          patientName={quoteBasket.name}
          items={quoteBasket.items}
          onClose={() => setQuoteBasket(null)}
          onSendToFilling={() => { setQuoteBasket(null) }}
        />
      )}
    </div>
  )
}
