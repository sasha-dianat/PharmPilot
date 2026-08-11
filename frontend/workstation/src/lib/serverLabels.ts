/**
 * English readings for vocabulary the backend sends in Persian only.
 *
 * Some labels reach the UI already translated — integrity-check titles, filter
 * chips, sort keys — because the server owns the vocabulary. Rather than
 * duplicate those catalogues server-side (and have the two drift apart the
 * first time one is renamed), the English reading is kept here and keyed by the
 * stable identifier the server also sends. An unknown key falls back to the
 * server's own text, so a newly added check or filter shows up untranslated
 * rather than blank.
 */

/** `reconciliation.ALL_CHECKS` — integrity findings. */
export const CHECK_TITLE_EN: Record<string, string> = {
  aggregate_drift: 'Aggregate stock disagrees with the sum of its lots',
  negative_stock: 'Negative stock',
  fill_without_movement: 'Fill with no stock deduction',
  untraceable_fill: 'Fill without lot traceability',
  dispense_shortfall: 'Stock shortfall at dispense',
  unbound_from_formulary: 'Not bound to the official formulary',
  expired_on_hand: 'Expired stock still sellable',
  suspicious_adjustment: 'Suspicious stock adjustments',
  duplicate_lot: 'Duplicate lot',
  unit_conversion_suspect: 'Possible unit-of-count error',
  over_reserved: 'Reserved beyond stock on hand',
  reservation_drift: 'Reserved aggregate disagrees with reservation rows',
  demand_signal_unsupported: 'Demand signal unsupported by dispense history',
  approval_overdue: 'Overdue approvals',
  ledger_chain: 'Ledger immutability chain',
}

/** `admin_rules.FILTERS` — the named views on the stock list. */
export const FILTER_LABEL_EN: Record<string, string> = {
  all: 'All items',
  below_par: 'Below reorder level',
  out_of_stock: 'Out of stock',
  expiring_soon: 'Expiring soon (90 days)',
  expired: 'Expired',
  quarantined: 'Quarantined',
  recalled: 'Recalled',
  controlled: 'Controlled',
  unbound: 'Not bound to the official list',
  dead_stock: 'Dead stock (180 days unused)',
  cold_chain: 'Cold chain broken',
  overstocked: 'Overstocked (over 365 days of cover)',
}

/** `admin_rules.SORTS` — sort keys, which arrive as bare identifiers. */
export const SORT_LABEL: Record<string, readonly [string, string]> = {
  name: ['Name', 'نام'],
  quantity: ['Quantity', 'موجودی'],
  expiry: ['Expiry', 'انقضا'],
  value: ['Value', 'ارزش'],
  days_supply: ['Days of cover', 'روز پوشش'],
  last_dispensed: ['Last dispensed', 'آخرین تحویل'],
}

/** Pick a reading, falling back to whatever the server sent. */
export function serverLabel(lang: 'fa' | 'en', map: Record<string, string>,
                            key: string, fallback: string): string {
  return lang === 'fa' ? fallback : (map[key] || fallback)
}

export function checkTitle(lang: 'fa' | 'en', check: string, titleFa: string): string {
  return serverLabel(lang, CHECK_TITLE_EN, check, titleFa)
}
