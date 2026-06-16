/**
 * Owner AI Provider Settings (Admin)
 * ==================================
 * Lets the pharmacy owner pick the AI service provider used by every AI surface
 * (Clinical Council, RAG, etc.) and set/rotate provider API keys. Keys are
 * write-only — the API never returns a raw key, only masked presence.
 * Backed by /api/v1/ai-settings (gated on staff:write).
 */
import { useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { apiClient } from '../lib/api'

interface ProviderCfg {
  provider_id: string
  name: string
  default_model: string
  has_baa: boolean
  has_api_key: boolean
  is_active: boolean
  is_preferred: boolean
  requires_key: boolean
}
interface Config {
  preferred_provider: string | null
  providers: ProviderCfg[]
}

export default function AIProviderSettings() {
  const qc = useQueryClient()
  const { data } = useQuery<Config>({
    queryKey: ['ai-settings'],
    queryFn: () => apiClient.get('/ai-settings/providers').then(r => r.data),
  })

  const [keyDrafts, setKeyDrafts] = useState<Record<string, string>>({})
  const [reveal, setReveal] = useState<Record<string, boolean>>({})
  const [savedMsg, setSavedMsg] = useState<string>('')

  const refresh = () => {
    qc.invalidateQueries({ queryKey: ['ai-settings'] })
    qc.invalidateQueries({ queryKey: ['ai-providers'] })
  }

  const setPreferred = useMutation({
    mutationFn: (provider: string) => apiClient.put('/ai-settings/preferred', { provider }),
    onSuccess: () => { setSavedMsg('Preferred provider updated'); refresh() },
  })
  const saveKey = useMutation({
    mutationFn: (p: { provider: string; api_key: string }) => apiClient.put('/ai-settings/key', p),
    onSuccess: (_d, vars) => {
      setSavedMsg(`API key saved for ${vars.provider}`)
      setKeyDrafts(s => ({ ...s, [vars.provider]: '' }))
      refresh()
    },
  })

  const providers = data?.providers ?? []
  const preferred = data?.preferred_provider ?? null

  return (
    <div className="bg-[#141925] rounded-xl p-4 border border-[#1e293b] space-y-4">
      <div className="flex items-center justify-between">
        <div>
          <p className="text-xs font-semibold uppercase tracking-widest text-slate-400">Owner · AI provider configuration</p>
          <p className="text-xs text-slate-500 mt-0.5">
            Choose the provider used by every AI surface (Clinical Council, RAG…) and set API keys.
            Preferred: <span className="text-slate-200 font-medium">{preferred ? (providers.find(p => p.provider_id === preferred)?.name ?? preferred) : 'task-default routing'}</span>
          </p>
        </div>
        {savedMsg && <span className="text-xs text-emerald-400">✓ {savedMsg}</span>}
      </div>

      <div className="space-y-2">
        {providers.map(p => (
          <div key={p.provider_id} className={`rounded-lg border p-3 ${p.is_preferred ? 'border-blue-500/60 bg-blue-950/20' : 'border-[#1e293b] bg-[#1a1f2e]'}`}>
            <div className="flex items-center gap-2 flex-wrap">
              <span className="font-medium text-slate-100">{p.name}</span>
              <span className="text-[10px] font-mono text-slate-500">{p.default_model}</span>
              {p.has_baa && <span className="text-[10px] px-1.5 py-0.5 rounded bg-emerald-900/40 text-emerald-300">BAA</span>}
              <span className={`text-[10px] px-1.5 py-0.5 rounded ${p.has_api_key ? 'bg-emerald-900/40 text-emerald-300' : 'bg-red-900/40 text-red-300'}`}>
                {p.requires_key ? (p.has_api_key ? 'Key set' : 'No key') : 'No key needed'}
              </span>
              {p.is_preferred && <span className="text-[10px] px-1.5 py-0.5 rounded bg-blue-900/50 text-blue-200">★ Preferred</span>}
              <button
                onClick={() => setPreferred.mutate(p.provider_id)}
                disabled={p.is_preferred || (p.requires_key && !p.has_api_key)}
                title={p.requires_key && !p.has_api_key ? 'Add an API key first' : 'Use this provider everywhere'}
                className="ml-auto text-xs px-2.5 py-1 rounded-md border border-slate-600 text-slate-200 hover:bg-slate-700/40 disabled:opacity-40 disabled:cursor-not-allowed"
              >
                {p.is_preferred ? 'In use' : 'Use this provider'}
              </button>
            </div>

            {p.requires_key && (
              <div className="flex items-center gap-2 mt-2">
                <input
                  type={reveal[p.provider_id] ? 'text' : 'password'}
                  value={keyDrafts[p.provider_id] ?? ''}
                  onChange={e => setKeyDrafts(s => ({ ...s, [p.provider_id]: e.target.value }))}
                  placeholder={p.has_api_key ? 'Enter a new key to rotate…' : `Paste ${p.name} API key…`}
                  className="flex-1 text-sm bg-[#0f1420] border border-[#243042] rounded-md px-3 py-1.5 text-slate-100 placeholder:text-slate-600 focus:outline-none focus:ring-2 focus:ring-blue-500/40 font-mono"
                />
                <button
                  onClick={() => setReveal(s => ({ ...s, [p.provider_id]: !s[p.provider_id] }))}
                  className="text-xs px-2 py-1.5 rounded-md border border-slate-700 text-slate-400 hover:bg-slate-700/40"
                  aria-label="Toggle key visibility"
                >{reveal[p.provider_id] ? '🙈' : '👁'}</button>
                <button
                  onClick={() => saveKey.mutate({ provider: p.provider_id, api_key: keyDrafts[p.provider_id] ?? '' })}
                  disabled={!(keyDrafts[p.provider_id] ?? '').trim() || saveKey.isPending}
                  className="text-xs px-3 py-1.5 rounded-md bg-blue-600 text-white hover:bg-blue-500 disabled:opacity-40 disabled:cursor-not-allowed"
                >Save key</button>
              </div>
            )}
          </div>
        ))}
      </div>

      <p className="text-[10px] text-slate-600 italic">
        Keys are stored server-side and never displayed again (masked). PHI-sensitive tasks only ever route to BAA-covered providers.
      </p>
    </div>
  )
}
