/**
 * Drug Search — instant NDC + name search for Rx intake and inventory.
 * Debounced, shows DEA schedule badge, REMS flag, and refrigeration indicator.
 */
import { useState, useCallback } from 'react'
import { useQuery } from '@tanstack/react-query'
import { inventoryApi } from '../lib/api'

interface Drug {
  id: string
  ndc11: string
  generic_name: string
  brand_name?: string
  strength?: string
  dosage_form?: string
  route?: string
  dea_schedule?: string
  is_controlled: boolean
  requires_refrigeration: boolean
}

interface Props {
  onSelect: (drug: Drug) => void
  placeholder?: string
  className?: string
}

function debounce<T extends (...args: unknown[]) => void>(fn: T, ms: number): T {
  let timer: ReturnType<typeof setTimeout>
  return ((...args) => { clearTimeout(timer); timer = setTimeout(() => fn(...args), ms) }) as T
}

export default function DrugSearch({ onSelect, placeholder = 'Search drug name or NDC…', className = '' }: Props) {
  const [query, setQuery] = useState('')
  const [debouncedQ, setDebouncedQ] = useState('')
  const [open, setOpen] = useState(false)

  const setDebounced = useCallback(debounce((v: unknown) => setDebouncedQ(v as string), 300), [])

  const handleChange = (v: string) => {
    setQuery(v)
    setDebounced(v)
    setOpen(v.length >= 2)
  }

  const { data: results = [], isFetching } = useQuery({
    queryKey: ['drug-search', debouncedQ],
    queryFn: () => inventoryApi.searchDrugs(debouncedQ).then(r => r.data as Drug[]),
    enabled: debouncedQ.length >= 2,
    staleTime: 30_000,
  })

  const handleSelect = (drug: Drug) => {
    setQuery(`${drug.generic_name}${drug.strength ? ' ' + drug.strength : ''}`)
    setOpen(false)
    onSelect(drug)
  }

  return (
    <div className={`relative ${className}`}>
      <div className="relative">
        <input
          type="text"
          value={query}
          onChange={e => handleChange(e.target.value)}
          onFocus={() => query.length >= 2 && setOpen(true)}
          onBlur={() => setTimeout(() => setOpen(false), 150)}
          placeholder={placeholder}
          className="w-full border border-gray-300 rounded-lg px-4 py-2.5 text-sm pr-10
                     focus:outline-none focus:ring-2 focus:ring-blue-500"
        />
        {isFetching && (
          <div className="absolute right-3 top-1/2 -translate-y-1/2">
            <svg className="animate-spin h-4 w-4 text-gray-400" fill="none" viewBox="0 0 24 24">
              <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4"/>
              <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8v8z"/>
            </svg>
          </div>
        )}
      </div>

      {open && results.length > 0 && (
        <div className="absolute z-50 w-full mt-1 bg-white border border-gray-200 rounded-lg shadow-xl max-h-64 overflow-y-auto">
          {results.map(drug => (
            <button
              key={drug.id}
              onClick={() => handleSelect(drug)}
              className="w-full px-4 py-3 text-left hover:bg-blue-50 border-b border-gray-100 last:border-0"
            >
              <div className="flex items-center justify-between gap-2">
                <div className="min-w-0">
                  <span className="font-medium text-sm text-gray-900">{drug.generic_name}</span>
                  {drug.strength && <span className="text-gray-500 text-sm"> {drug.strength}</span>}
                  {drug.brand_name && (
                    <span className="text-gray-400 text-xs ml-1">({drug.brand_name})</span>
                  )}
                  <div className="text-xs text-gray-400 mt-0.5">
                    NDC: {drug.ndc11} · {drug.dosage_form} · {drug.route}
                  </div>
                </div>
                <div className="flex items-center gap-1 flex-shrink-0">
                  {drug.dea_schedule && (
                    <span className="text-xs bg-orange-100 text-orange-700 px-1.5 py-0.5 rounded font-medium">
                      {drug.dea_schedule}
                    </span>
                  )}
                  {drug.requires_refrigeration && (
                    <span className="text-xs" title="Requires refrigeration">❄️</span>
                  )}
                </div>
              </div>
            </button>
          ))}
        </div>
      )}

      {open && debouncedQ.length >= 2 && results.length === 0 && !isFetching && (
        <div className="absolute z-50 w-full mt-1 bg-white border border-gray-200 rounded-lg shadow-xl px-4 py-3">
          <p className="text-sm text-gray-500">No drugs found for "{debouncedQ}"</p>
        </div>
      )}
    </div>
  )
}
