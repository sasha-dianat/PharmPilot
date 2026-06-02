/**
 * Inventory Panel — stock levels, expiry alerts, quick reorder.
 * Embedded in workstation sidebar for quick pharmacist reference.
 */
import { useQuery } from '@tanstack/react-query'
import { inventoryApi } from '../lib/api'

interface ExpiringLot {
  ndc11: string
  lot_number: string
  expiry_date: string
  days_until_expiry: number
  quantity_on_hand: number
  urgency: 'immediate' | 'high' | 'moderate' | 'low'
}

interface StockLevel {
  ndc11: string
  quantity_on_hand: number
  quantity_reserved: number
  reorder_point?: number
  stockout_risk?: number
  last_dispensed_at?: string
}

const URGENCY_STYLE = {
  immediate: 'bg-red-100 text-red-700 border-red-200',
  high:      'bg-orange-100 text-orange-700 border-orange-200',
  moderate:  'bg-yellow-100 text-yellow-700 border-yellow-200',
  low:       'bg-blue-50 text-blue-700 border-blue-200',
}

export default function InventoryPanel() {
  const { data: expiring = [] } = useQuery({
    queryKey: ['expiring', 30],
    queryFn: () => inventoryApi.getExpiring(30).then(r => r.data as ExpiringLot[]),
    staleTime: 60_000,
  })

  const { data: lowStock = [] } = useQuery({
    queryKey: ['low-stock'],
    queryFn: () => inventoryApi.getStock().then(r =>
      (r.data as StockLevel[]).filter(s => s.reorder_point && s.quantity_on_hand <= s.reorder_point)
    ),
    staleTime: 60_000,
  })

  const criticalExpiring = expiring.filter(e => e.urgency === 'immediate' || e.urgency === 'high')
  const totalAlerts = criticalExpiring.length + lowStock.length

  return (
    <div className="h-full overflow-y-auto">
      <div className="px-3 py-3 border-b border-gray-100 flex items-center justify-between">
        <div className="flex items-center gap-2">
          <span className="text-base">📦</span>
          <span className="font-semibold text-sm text-gray-800">Inventory</span>
        </div>
        {totalAlerts > 0 && (
          <span className="text-xs bg-red-100 text-red-700 px-2 py-0.5 rounded-full font-medium">
            {totalAlerts} alert{totalAlerts > 1 ? 's' : ''}
          </span>
        )}
      </div>

      <div className="p-3 space-y-4">
        {/* Expiry alerts */}
        {expiring.length > 0 && (
          <div>
            <p className="text-xs font-semibold text-gray-500 uppercase tracking-wider mb-2">
              Expiring Soon
            </p>
            <div className="space-y-1.5">
              {expiring.slice(0, 8).map((lot, i) => (
                <div key={i} className={`rounded-lg px-3 py-2 border text-xs ${URGENCY_STYLE[lot.urgency]}`}>
                  <div className="flex justify-between items-center">
                    <span className="font-mono font-medium">{lot.ndc11}</span>
                    <span className="font-semibold">
                      {lot.days_until_expiry <= 0 ? 'EXPIRED' : `${lot.days_until_expiry}d`}
                    </span>
                  </div>
                  <div className="text-opacity-75 mt-0.5">
                    Lot {lot.lot_number} · {lot.quantity_on_hand} units · {lot.expiry_date}
                  </div>
                </div>
              ))}
            </div>
          </div>
        )}

        {/* Low stock / reorder needed */}
        {lowStock.length > 0 && (
          <div>
            <p className="text-xs font-semibold text-gray-500 uppercase tracking-wider mb-2">
              Reorder Needed
            </p>
            <div className="space-y-1.5">
              {lowStock.slice(0, 6).map((s, i) => (
                <div key={i} className="rounded-lg px-3 py-2 border border-orange-200 bg-orange-50 text-xs">
                  <div className="flex justify-between">
                    <span className="font-mono font-medium text-orange-800">{s.ndc11}</span>
                    <span className="text-orange-600">
                      {s.quantity_on_hand} / {s.reorder_point} min
                    </span>
                  </div>
                  {s.stockout_risk && s.stockout_risk > 0.5 && (
                    <div className="text-orange-600 mt-0.5">
                      ⚠ {(s.stockout_risk * 100).toFixed(0)}% stockout risk (7 days)
                    </div>
                  )}
                </div>
              ))}
            </div>
          </div>
        )}

        {expiring.length === 0 && lowStock.length === 0 && (
          <div className="text-center py-6 text-gray-400 text-xs">
            <div className="text-2xl mb-1">✅</div>
            No inventory alerts
          </div>
        )}
      </div>
    </div>
  )
}
