/**
 * PharmPilot Admin Portal — Multi-pharmacy chain management
 * Port 3002 | Enterprise admin interface
 */
import { useState } from 'react'
import { QueryClient, QueryClientProvider, useQuery } from '@tanstack/react-query'
import axios from 'axios'

const queryClient = new QueryClient()
const api = axios.create({ baseURL: import.meta.env.VITE_API_URL || 'http://localhost:8001/api/v1' })

function ChainDashboard() {
  const { data: summary } = useQuery({
    queryKey: ['chain-summary'],
    queryFn: () => api.get('/analytics/dashboard/operational').then(r => r.data),
    refetchInterval: 30_000,
  })
  const metrics = [
    { label: 'Rxs Today',            value: summary?.fills?.today ?? '—',        icon: '💊' },
    { label: 'Queue Depth',          value: summary?.fills?.queue_depth ?? '—',   icon: '⏳' },
    { label: 'Rejected Claims',      value: summary?.claims?.rejected_today ?? '—', icon: '⚠️' },
    { label: 'Avg Adjudication',     value: summary?.claims?.avg_adjudication_ms ? `${summary.claims.avg_adjudication_ms}ms` : '—', icon: '⚡' },
    { label: 'Expiring Lots (30d)',   value: summary?.inventory?.expiring_lots_30d ?? '—', icon: '📅' },
    { label: 'Stockouts',            value: summary?.inventory?.stockouts ?? '—', icon: '🚫' },
  ]
  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-bold text-gray-900">Chain Overview</h1>
        <p className="text-sm text-gray-500 mt-1">Real-time metrics across all locations</p>
      </div>
      <div className="grid grid-cols-2 lg:grid-cols-3 gap-4">
        {metrics.map(m => (
          <div key={m.label} className="bg-white rounded-xl border border-gray-200 p-5">
            <div className="flex items-center justify-between">
              <span className="text-2xl">{m.icon}</span>
              <span className="text-3xl font-bold text-gray-900">{m.value}</span>
            </div>
            <p className="text-sm text-gray-500 mt-2">{m.label}</p>
          </div>
        ))}
      </div>
    </div>
  )
}

function Sidebar({ active, onNav }: { active: string; onNav: (s: string) => void }) {
  const items = [
    { id: 'overview',     label: 'Chain Overview',   icon: '🏢' },
    { id: 'pharmacies',   label: 'Pharmacies',        icon: '💊' },
    { id: 'staff',        label: 'Staff',             icon: '👥' },
    { id: 'analytics',    label: 'Analytics',         icon: '📊' },
    { id: 'star-ratings', label: 'CMS Stars',         icon: '⭐' },
    { id: 'security',     label: 'Security',          icon: '🛡️' },
    { id: 'knowledge',    label: 'Knowledge Base',    icon: '🧠' },
    { id: 'settings',     label: 'Settings',          icon: '⚙️' },
  ]
  return (
    <div className="w-56 flex-shrink-0 bg-slate-900 min-h-screen flex flex-col">
      <div className="p-5 border-b border-slate-800">
        <div className="text-white font-bold text-lg">💊 PharmPilot</div>
        <div className="text-slate-400 text-xs mt-1">Admin Portal</div>
      </div>
      <nav className="flex-1 p-3">
        {items.map(item => (
          <button key={item.id} onClick={() => onNav(item.id)}
            className={`w-full flex items-center gap-3 px-3 py-2.5 rounded-lg text-sm mb-1 transition-colors
              ${active === item.id ? 'bg-blue-600 text-white' : 'text-slate-400 hover:text-white hover:bg-slate-800'}`}>
            <span>{item.icon}</span><span>{item.label}</span>
          </button>
        ))}
      </nav>
      <div className="p-4 border-t border-slate-800 text-slate-500 text-xs">PharmPilot v1.0 · Admin</div>
    </div>
  )
}

function AdminApp() {
  const [section, setSection] = useState('overview')
  return (
    <div className="flex min-h-screen bg-gray-50">
      <Sidebar active={section} onNav={setSection} />
      <main className="flex-1 p-8 overflow-y-auto">
        {section === 'overview' && <ChainDashboard />}
        {section !== 'overview' && (
          <div className="flex items-center justify-center h-64 text-gray-400">
            <div className="text-center">
              <div className="text-5xl mb-3">🚧</div>
              <p className="text-lg font-medium text-gray-700 capitalize">{section}</p>
              <p className="text-sm mt-1">Frontend rendering phase — backend ready</p>
            </div>
          </div>
        )}
      </main>
    </div>
  )
}

export default function App() {
  return <QueryClientProvider client={queryClient}><AdminApp /></QueryClientProvider>
}
