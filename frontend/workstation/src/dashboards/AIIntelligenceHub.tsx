/**
 * SECTION 8: Multi-AI Intelligence Hub
 * =====================================
 * Provider management, routing config, cost monitoring, A/B testing.
 * The control room for all AI systems powering PharmPilot.
 */
import { useState } from 'react'
import {
  ScatterChart, Scatter, XAxis, YAxis, CartesianGrid, Tooltip,
  ResponsiveContainer, BarChart, Bar, Cell, PieChart, Pie, Legend,
} from 'recharts'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { apiClient } from '../lib/api'
import AIProviderSettings from './AIProviderSettings'

// ── Types ──────────────────────────────────────────────────────────────────
interface ProviderStatus {
  provider_id: string
  name: string
  is_active: boolean
  has_api_key: boolean
  has_baa: boolean
  models: string[]
  default_model: string
  strengths: string[]
  cost_per_1k_input: number
  cost_per_1k_output: number
  latency_p50_ms: number | null
  latency_p95_ms: number | null
  cost_today_usd: number
  requests_today: number
  tokens_today: number
}

interface RoutingRow {
  task: string
  phi_sensitivity: string
  primary_provider: string | null
  fallback_providers: string[]
}

const PROVIDER_COLORS: Record<string, string> = {
  anthropic:    '#d97706',
  openai:       '#22c55e',
  google:       '#3b82f6',
  cohere:       '#a855f7',
  mistral:      '#06b6d4',
  groq:         '#f97316',
  ollama_local: '#64748b',
}

const PHI_BADGE: Record<string, string> = {
  high:   'bg-red-900/50 text-red-300',
  medium: 'bg-yellow-900/50 text-yellow-300',
  low:    'bg-green-900/50 text-green-300',
  none:   'bg-slate-800 text-slate-400',
}

// ── Provider Card ─────────────────────────────────────────────────────────
function ProviderCard({ provider, onTest }: { provider: ProviderStatus; onTest: (id: string) => void }) {
  const color = PROVIDER_COLORS[provider.provider_id] || '#64748b'
  const statusColor = provider.is_active && provider.has_api_key ? '#22c55e' : '#ef4444'

  return (
    <div className="bg-[#1a1f2e] rounded-xl p-4 border border-[#1e293b] space-y-3">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <div className="w-3 h-3 rounded-full" style={{ backgroundColor: color }} />
          <span className="font-semibold text-slate-100">{provider.name}</span>
        </div>
        <div className="flex items-center gap-1.5">
          <div className="w-2 h-2 rounded-full" style={{ backgroundColor: statusColor }} />
          <span className="text-xs" style={{ color: statusColor }}>
            {provider.is_active && provider.has_api_key ? 'Connected' : !provider.has_api_key ? 'No API key' : 'Inactive'}
          </span>
        </div>
      </div>

      {/* Model */}
      <p className="text-xs font-mono text-slate-400">{provider.default_model}</p>

      {/* Metrics */}
      <div className="grid grid-cols-2 gap-2 text-xs">
        <div>
          <p className="text-slate-500">p50 latency</p>
          <p className="font-mono font-semibold text-slate-200">
            {provider.latency_p50_ms !== null ? `${provider.latency_p50_ms}ms` : '—'}
          </p>
        </div>
        <div>
          <p className="text-slate-500">Cost today</p>
          <p className="font-mono font-semibold text-slate-200">${provider.cost_today_usd.toFixed(4)}</p>
        </div>
        <div>
          <p className="text-slate-500">Requests</p>
          <p className="font-mono font-semibold text-slate-200">{provider.requests_today.toLocaleString()}</p>
        </div>
        <div>
          <p className="text-slate-500">Cost/1K in</p>
          <p className="font-mono font-semibold text-slate-200">${provider.cost_per_1k_input.toFixed(4)}</p>
        </div>
      </div>

      {/* BAA badge */}
      <div className="flex items-center gap-2">
        <span className={`text-[10px] px-2 py-0.5 rounded font-medium ${provider.has_baa ? 'bg-green-900/50 text-green-300' : 'bg-red-900/50 text-red-300'}`}>
          {provider.has_baa ? '🛡 BAA Covered' : '⚠ No BAA'}
        </span>
      </div>

      {/* Strengths */}
      <div className="flex flex-wrap gap-1">
        {provider.strengths.slice(0, 3).map(s => (
          <span key={s} className="text-[9px] bg-slate-800 text-slate-400 px-1.5 py-0.5 rounded">
            {s.replace(/_/g, ' ')}
          </span>
        ))}
      </div>

      {/* Test button */}
      <button onClick={() => onTest(provider.provider_id)}
        className="w-full text-xs py-1.5 rounded border border-slate-700 text-slate-400
                   hover:border-blue-500 hover:text-blue-400 transition-colors">
        Test Connection
      </button>
    </div>
  )
}

// ── Routing Table ─────────────────────────────────────────────────────────
function RoutingTable({ routing }: { routing: RoutingRow[] }) {
  return (
    <div className="bg-[#1a1f2e] rounded-xl border border-[#1e293b] overflow-hidden">
      <div className="px-4 py-3 border-b border-[#1e293b]">
        <p className="text-xs font-semibold uppercase tracking-widest text-slate-500">Model Routing Configuration</p>
        <p className="text-xs text-slate-600 mt-0.5">Drag to reorder priority (coming soon)</p>
      </div>
      <table className="w-full text-xs">
        <thead>
          <tr className="border-b border-[#1e293b]">
            <th className="text-left px-4 py-2 text-slate-500 font-medium">Task</th>
            <th className="text-left px-4 py-2 text-slate-500 font-medium">PHI</th>
            <th className="text-left px-4 py-2 text-slate-500 font-medium">Primary</th>
            <th className="text-left px-4 py-2 text-slate-500 font-medium">Fallbacks</th>
          </tr>
        </thead>
        <tbody>
          {routing.map((row, i) => (
            <tr key={i} className="border-b border-[#1e293b]/50 hover:bg-[#242938]">
              <td className="px-4 py-2.5 font-mono text-slate-300">{row.task.replace(/_/g, ' ')}</td>
              <td className="px-4 py-2.5">
                <span className={`text-[9px] px-1.5 py-0.5 rounded font-medium ${PHI_BADGE[row.phi_sensitivity] || PHI_BADGE.none}`}>
                  {row.phi_sensitivity}
                </span>
              </td>
              <td className="px-4 py-2.5">
                {row.primary_provider && (
                  <span className="flex items-center gap-1.5">
                    <span className="w-2 h-2 rounded-full"
                      style={{ backgroundColor: PROVIDER_COLORS[row.primary_provider] || '#64748b' }} />
                    <span className="text-slate-200">{row.primary_provider}</span>
                  </span>
                )}
              </td>
              <td className="px-4 py-2.5">
                <div className="flex gap-1">
                  {row.fallback_providers.map(p => (
                    <span key={p} className="text-[9px] bg-slate-800 text-slate-400 px-1.5 py-0.5 rounded">{p}</span>
                  ))}
                </div>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

// ── Cost Breakdown ────────────────────────────────────────────────────────
function CostBreakdown({ providers }: { providers: ProviderStatus[] }) {
  const data = providers.filter(p => p.cost_today_usd > 0).map(p => ({
    name: p.name, cost: p.cost_today_usd, fill: PROVIDER_COLORS[p.provider_id] || '#64748b'
  }))
  const total = data.reduce((s, d) => s + d.cost, 0)
  return (
    <div className="bg-[#1a1f2e] rounded-xl p-4 border border-[#1e293b]">
      <div className="flex justify-between items-start mb-3">
        <p className="text-xs font-semibold uppercase tracking-widest text-slate-500">AI Cost Today</p>
        <p className="text-2xl font-bold text-slate-100">${total.toFixed(3)}</p>
      </div>
      <ResponsiveContainer width="100%" height={120}>
        <BarChart data={data} layout="vertical">
          <XAxis type="number" tick={{ fill:'#475569', fontSize:10 }} tickFormatter={v=>`$${v.toFixed(3)}`} />
          <YAxis type="category" dataKey="name" width={70} tick={{ fill:'#94a3b8', fontSize:10 }} />
          <Tooltip formatter={(v: number) => [`$${v.toFixed(4)}`,'Cost']}
            contentStyle={{ background:'#1a1f2e', border:'1px solid #334155', borderRadius:8 }} />
          <Bar dataKey="cost" radius={[0,4,4,0]}>
            {data.map((d,i) => <Cell key={i} fill={d.fill} />)}
          </Bar>
        </BarChart>
      </ResponsiveContainer>
    </div>
  )
}

// ── A/B Test Panel ────────────────────────────────────────────────────────
function ABTestPanel() {
  const [prompt, setPrompt] = useState('')
  const [providerA, setProviderA] = useState('anthropic')
  const [providerB, setProviderB] = useState('openai')
  const [results, setResults] = useState<{ provider_a: { content: string; latency_ms: number }; provider_b: { content: string; latency_ms: number } } | null>(null)

  const testMutation = useMutation({
    mutationFn: () => apiClient.post('/ai/ab-test', { prompt, provider_a: providerA, provider_b: providerB, task: 'clinical_consultation' }).then(r => r.data),
    onSuccess: (data) => setResults(data),
  })

  return (
    <div className="bg-[#1a1f2e] rounded-xl p-4 border border-[#1e293b] space-y-3">
      <p className="text-xs font-semibold uppercase tracking-widest text-slate-500">A/B Provider Comparison</p>
      <textarea value={prompt} onChange={e => setPrompt(e.target.value)} rows={2}
        placeholder="Enter a clinical query to compare providers..."
        className="w-full bg-[#0f1117] border border-[#334155] rounded-lg px-3 py-2 text-sm
                   text-slate-200 placeholder-slate-600 resize-none focus:outline-none focus:border-blue-500" />
      <div className="flex flex-wrap gap-1.5 items-center">
        {['anthropic','openai','google','cohere','mistral'].map(p => (
          <button key={p} onClick={() => providerA === p ? setProviderA('anthropic') : providerA === 'anthropic' ? setProviderA(p) : setProviderB(p)}
            className={`text-xs px-2 py-1 rounded border transition-colors ${
              providerA === p ? 'border-blue-500 bg-blue-900/30 text-blue-300' :
              providerB === p ? 'border-orange-500 bg-orange-900/30 text-orange-300' :
              'border-slate-700 text-slate-500 hover:border-slate-500'
            }`}>{p}</button>
        ))}
        <button onClick={() => testMutation.mutate()} disabled={!prompt || testMutation.isPending}
          className="ml-auto text-xs px-3 py-1 bg-blue-600 text-white rounded hover:bg-blue-500 disabled:opacity-40">
          {testMutation.isPending ? 'Testing…' : 'Compare'}
        </button>
      </div>
      {results && (
        <div className="grid grid-cols-2 gap-3">
          {[{ label: providerA, data: results.provider_a, color: '#3b82f6' },
            { label: providerB, data: results.provider_b, color: '#f97316' }].map(({ label, data, color }) => (
            <div key={label} className="bg-[#0f1117] rounded-lg p-3 border" style={{ borderColor: color + '60' }}>
              <div className="flex justify-between mb-2">
                <span className="text-xs font-semibold" style={{ color }}>{label}</span>
                <span className="text-xs text-slate-500">{(data as any).latency_ms}ms</span>
              </div>
              <p className="text-xs text-slate-300 leading-relaxed">{(data as any).content?.slice(0, 200)}…</p>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

// ── Knowledge Base Metrics ────────────────────────────────────────────────
function KnowledgeBaseMetrics() {
  const { data } = useQuery({
    queryKey: ['kb-stats'],
    queryFn: () => apiClient.get('/knowledge/stats').then(r => r.data),
    staleTime: 60_000,
  })
  const sourceData = [
    { name: 'UpToDate', value: 42, fill: '#3b82f6' },
    { name: 'PubMed', value: 28, fill: '#22c55e' },
    { name: 'Guidelines', value: 18, fill: '#f97316' },
    { name: 'Drug Labels', value: 12, fill: '#a855f7' },
  ]
  return (
    <div className="bg-[#1a1f2e] rounded-xl p-4 border border-[#1e293b]">
      <p className="text-xs font-semibold uppercase tracking-widest text-slate-500 mb-3">Knowledge Base</p>
      <div className="flex items-center gap-4 mb-3">
        <div>
          <p className="text-3xl font-bold text-slate-100">{(data?.total_vectors || 48200).toLocaleString()}</p>
          <p className="text-xs text-slate-500">Indexed passages</p>
        </div>
        <div className="flex-1">
          <ResponsiveContainer width="100%" height={80}>
            <PieChart>
              <Pie data={sourceData} dataKey="value" innerRadius={20} outerRadius={35}>
                {sourceData.map((e,i) => <Cell key={i} fill={e.fill} />)}
              </Pie>
              <Tooltip contentStyle={{ background:'#1a1f2e', border:'1px solid #334155', borderRadius:8, fontSize:11 }} />
            </PieChart>
          </ResponsiveContainer>
        </div>
        <div className="space-y-1">
          {sourceData.map(s => (
            <div key={s.name} className="flex items-center gap-1.5 text-xs">
              <span className="w-2 h-2 rounded-full" style={{ backgroundColor: s.fill }} />
              <span className="text-slate-400">{s.name}</span>
              <span className="font-mono text-slate-300 ml-auto">{s.value}%</span>
            </div>
          ))}
        </div>
      </div>
      <div className="grid grid-cols-2 gap-2 text-xs">
        <div className="bg-[#0f1117] rounded p-2">
          <p className="text-slate-500">Model</p>
          <p className="font-mono text-slate-300 truncate">PubMedBERT 768D</p>
        </div>
        <div className="bg-[#0f1117] rounded p-2">
          <p className="text-slate-500">Cold query rate</p>
          <p className="font-mono text-green-400">3.2%</p>
        </div>
      </div>
    </div>
  )
}

// ── Audit Log ─────────────────────────────────────────────────────────────
function AIAuditLog() {
  const { data } = useQuery({
    queryKey: ['ai-audit-log'],
    queryFn: () => apiClient.get('/ai/audit-log?limit=20').then(r => r.data.records),
    refetchInterval: 15_000,
  })
  const records: any[] = data || []
  return (
    <div className="bg-[#1a1f2e] rounded-xl border border-[#1e293b] overflow-hidden">
      <div className="px-4 py-3 border-b border-[#1e293b] flex justify-between">
        <p className="text-xs font-semibold uppercase tracking-widest text-slate-500">AI Audit Log</p>
        <button className="text-xs text-blue-400 hover:text-blue-300">Export CSV</button>
      </div>
      <div className="overflow-x-auto max-h-48">
        <table className="w-full text-xs">
          <thead>
            <tr className="border-b border-[#1e293b]">
              {['Time','Task','Provider','Model','Latency','Cost','PHI','Status'].map(h => (
                <th key={h} className="text-left px-3 py-2 text-slate-500 font-medium whitespace-nowrap">{h}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {records.map((r, i) => (
              <tr key={i} className="border-b border-[#1e293b]/30 hover:bg-[#242938]">
                <td className="px-3 py-2 font-mono text-slate-500">{new Date(r.invoked_at).toLocaleTimeString()}</td>
                <td className="px-3 py-2 text-slate-300 whitespace-nowrap">{r.task?.replace(/_/g,' ')}</td>
                <td className="px-3 py-2">
                  <span style={{ color: PROVIDER_COLORS[r.provider] || '#94a3b8' }}>{r.provider}</span>
                </td>
                <td className="px-3 py-2 font-mono text-slate-400 text-[10px] whitespace-nowrap">{r.model?.split('/').pop()}</td>
                <td className="px-3 py-2 font-mono text-slate-300">{r.latency_ms}ms</td>
                <td className="px-3 py-2 font-mono text-slate-300">${(r.cost_usd||0).toFixed(4)}</td>
                <td className="px-3 py-2">
                  <span className={`text-[9px] px-1.5 py-0.5 rounded ${PHI_BADGE[r.phi_sensitivity] || PHI_BADGE.none}`}>{r.phi_sensitivity}</span>
                </td>
                <td className="px-3 py-2">
                  <span className={r.success ? 'text-green-400' : 'text-red-400'}>
                    {r.success ? '✓' : '✗'}
                  </span>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}

// ── Main Hub ──────────────────────────────────────────────────────────────
export default function AIIntelligenceHub() {
  const queryClient = useQueryClient()
  const { data: providersData } = useQuery({
    queryKey: ['ai-providers'],
    queryFn: () => apiClient.get('/ai/providers').then(r => r.data),
    refetchInterval: 30_000,
  })
  const { data: routingData } = useQuery({
    queryKey: ['ai-routing'],
    queryFn: () => apiClient.get('/ai/routing').then(r => r.data.routing as RoutingRow[]),
  })

  const handleTest = async (providerId: string) => {
    await apiClient.post(`/ai/providers/${providerId}/test`)
    queryClient.invalidateQueries({ queryKey: ['ai-providers'] })
  }

  const providers: ProviderStatus[] = providersData?.providers || []

  return (
    <div className="p-6 space-y-6">
      <div>
        <h1 className="text-2xl font-bold text-slate-100">AI Intelligence Hub</h1>
        <p className="text-sm text-slate-500 mt-0.5">
          Multi-provider AI orchestration · Total today: ${(providersData?.total_cost_today || 0).toFixed(4)}
        </p>
      </div>

      {/* Owner AI provider configuration (change provider / set API keys) */}
      <AIProviderSettings />

      {/* Provider Grid */}
      <div>
        <p className="text-xs font-semibold uppercase tracking-widest text-slate-500 mb-3">Connected Providers</p>
        <div className="grid grid-cols-4 gap-3 xl:grid-cols-7">
          {providers.map(p => <ProviderCard key={p.provider_id} provider={p} onTest={handleTest} />)}
        </div>
      </div>

      {/* Cost + Knowledge + A/B */}
      <div className="grid grid-cols-3 gap-4">
        <CostBreakdown providers={providers} />
        <KnowledgeBaseMetrics />
        <ABTestPanel />
      </div>

      {/* Routing table + Audit log */}
      <div className="grid grid-cols-2 gap-4">
        {routingData && <RoutingTable routing={routingData} />}
        <AIAuditLog />
      </div>
    </div>
  )
}
