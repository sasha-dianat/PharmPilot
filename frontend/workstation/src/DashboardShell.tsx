/**
 * PharmPilot Dashboard Shell
 * ===========================
 * Main navigation hub connecting all 8 dashboard sections.
 * Left sidebar with section nav. Top bar with live status + AI cost.
 * Keyboard: 1–8 switches sections. Escape returns to workstation.
 */
import { useState, useEffect } from 'react'
import { useQuery } from '@tanstack/react-query'
import { apiClient } from './lib/api'
import { ErrorBoundary } from './components/ErrorBoundary'
import CommandCenter         from './dashboards/CommandCenter'
import InventoryIntelligence from './dashboards/InventoryIntelligence'
import ClinicalIntelligence  from './dashboards/ClinicalIntelligence'
import SecuritySurveillance  from './dashboards/SecuritySurveillance'
import FinancialOperations   from './dashboards/FinancialOperations'
import PatientAdherence      from './dashboards/PatientAdherence'
import AudioIntelligence     from './dashboards/AudioIntelligence'
import AIIntelligenceHub     from './dashboards/AIIntelligenceHub'
import KnowledgeManager      from './dashboards/KnowledgeManager'

const SECTIONS = [
  { id:'command',    key:'1', label:'Command Center',     icon:'🏠', description:'Owner overview',      shortcut:'Alt+1' },
  { id:'inventory',  key:'2', label:'Inventory AI',       icon:'📦', description:'ML stock brain',       shortcut:'Alt+2' },
  { id:'clinical',   key:'3', label:'Clinical Intel',     icon:'🧠', description:'Patient safety',       shortcut:'Alt+3' },
  { id:'security',   key:'4', label:'Surveillance',       icon:'🛡️', description:'Security & safety',   shortcut:'Alt+4' },
  { id:'financial',  key:'5', label:'Financial Ops',      icon:'💰', description:'Revenue & claims',     shortcut:'Alt+5' },
  { id:'adherence',  key:'6', label:'Patient Care',       icon:'👥', description:'Adherence & MTM',      shortcut:'Alt+6' },
  { id:'audio',      key:'7', label:'Conversation AI',    icon:'🎙️', description:'Transcript review',   shortcut:'Alt+7' },
  { id:'ai-hub',     key:'8', label:'AI Hub',             icon:'🤖', description:'Multi-AI control',     shortcut:'Alt+8' },
  { id:'knowledge',  key:'9', label:'Knowledge Base',     icon:'📚', description:'Train clinical brain', shortcut:'Alt+9' },
] as const

type SectionId = typeof SECTIONS[number]['id']

const SECTION_COMPONENTS: Record<SectionId, React.ComponentType> = {
  command:   CommandCenter,
  inventory: InventoryIntelligence,
  clinical:  ClinicalIntelligence,
  security:  SecuritySurveillance,
  financial: FinancialOperations,
  adherence: PatientAdherence,
  audio:     AudioIntelligence,
  'ai-hub':  AIIntelligenceHub,
  knowledge: KnowledgeManager,
}

interface Props {
  onExitDashboard?: () => void
}

export default function DashboardShell({ onExitDashboard }: Props) {
  const [activeSection, setActiveSection] = useState<SectionId>('command')
  const userRole = localStorage.getItem('user_role') || ''
  const pharmacyId = localStorage.getItem('pharmacy_id') || ''

  // Live status polls
  const { data: summary } = useQuery({
    queryKey: ['security-summary-shell'],
    queryFn: () => apiClient.get('/security/summary').then(r => r.data),
    refetchInterval: 15_000,
  })
  const { data: aiData } = useQuery({
    queryKey: ['ai-providers-shell'],
    queryFn: () => apiClient.get('/ai/providers').then(r => r.data),
    refetchInterval: 60_000,
  })

  // Keyboard shortcuts: Alt+1..8, Escape
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if (e.altKey && e.key >= '1' && e.key <= '9') {
        const section = SECTIONS[parseInt(e.key) - 1]
        if (section) setActiveSection(section.id)
        e.preventDefault()
      }
      if (e.key === 'Escape' && onExitDashboard) onExitDashboard()
    }
    window.addEventListener('keydown', handler)
    return () => window.removeEventListener('keydown', handler)
  }, [onExitDashboard])

  const ActiveComponent = SECTION_COMPONENTS[activeSection]
  const criticalAlerts  = summary?.critical_unresolved || 0
  const totalAICost     = aiData?.total_cost_today || 0

  // Section alert badges
  const sectionBadges: Partial<Record<SectionId, string>> = {
    security: criticalAlerts > 0 ? String(criticalAlerts) : undefined,
    audio:    '3',  // Pending profile updates
    clinical: '2',  // REMS blockers
  }

  return (
    <div className="flex h-screen bg-[#0f1117] text-slate-100 overflow-hidden">

      {/* ── Left Sidebar ──────────────────────────────────────────────── */}
      <nav className="w-52 flex-shrink-0 bg-[#0d1117] border-r border-[#1e293b] flex flex-col">
        {/* Logo */}
        <div className="px-4 py-4 border-b border-[#1e293b]">
          <div className="flex items-center gap-2">
            <span className="text-xl">💊</span>
            <div>
              <span className="font-bold text-slate-100 text-sm">PharmPilot</span>
              <p className="text-[10px] text-slate-500">AI Dashboard</p>
            </div>
          </div>
        </div>

        {/* Section navigation */}
        <div className="flex-1 overflow-y-auto py-3 px-2">
          <p className="text-[9px] font-semibold uppercase tracking-widest text-slate-600 px-2 mb-2">Dashboards</p>
          {SECTIONS.map(section => {
            const isActive = activeSection === section.id
            const badge    = sectionBadges[section.id]
            return (
              <button key={section.id} onClick={() => setActiveSection(section.id)}
                className={`w-full flex items-center gap-2.5 px-3 py-2.5 rounded-lg mb-0.5 text-left transition-colors group
                  ${isActive
                    ? 'bg-blue-600 text-white'
                    : 'text-slate-400 hover:text-slate-200 hover:bg-[#1a1f2e]'
                  }`}
                aria-label={`${section.label} (${section.shortcut})`}
              >
                <span className="text-base flex-shrink-0">{section.icon}</span>
                <div className="flex-1 min-w-0">
                  <p className="text-xs font-medium leading-tight truncate">{section.label}</p>
                  <p className={`text-[9px] leading-tight truncate ${isActive ? 'text-blue-200' : 'text-slate-600'}`}>
                    {section.description}
                  </p>
                </div>
                {badge && (
                  <span className={`flex-shrink-0 text-[9px] font-bold px-1.5 py-0.5 rounded-full
                    ${section.id === 'security' && criticalAlerts > 0
                      ? 'bg-red-500 text-white'
                      : 'bg-orange-500 text-white'}`}>
                    {badge}
                  </span>
                )}
                {!isActive && (
                  <span className="text-[8px] text-slate-700 group-hover:text-slate-500 flex-shrink-0">
                    ⌥{section.key}
                  </span>
                )}
              </button>
            )
          })}
        </div>

        {/* Footer: AI cost + role */}
        <div className="px-3 py-3 border-t border-[#1e293b] space-y-2">
          <div className="flex justify-between text-[10px]">
            <span className="text-slate-500">AI cost today</span>
            <span className="font-mono text-slate-300">${totalAICost.toFixed(3)}</span>
          </div>
          <div className="flex justify-between text-[10px]">
            <span className="text-slate-500">Role</span>
            <span className="capitalize text-slate-400">{userRole.replace(/_/g,' ')}</span>
          </div>
          {onExitDashboard && (
            <button onClick={onExitDashboard}
              className="w-full text-[10px] text-slate-500 hover:text-slate-300 py-1 text-center">
              ← Workstation (Esc)
            </button>
          )}
        </div>
      </nav>

      {/* ── Main content ──────────────────────────────────────────────── */}
      <div className="flex-1 flex flex-col overflow-hidden">
        {/* Top bar */}
        <header className="flex-shrink-0 h-10 bg-[#0d1117] border-b border-[#1e293b]
                           flex items-center justify-between px-6 text-xs">
          <div className="flex items-center gap-3">
            <span className="font-semibold text-slate-200">
              {SECTIONS.find(s => s.id === activeSection)?.label}
            </span>
            {criticalAlerts > 0 && (
              <span className="bg-red-500 text-white px-2 py-0.5 rounded-full font-bold animate-pulse text-[10px]">
                {criticalAlerts} CRITICAL
              </span>
            )}
          </div>
          <div className="flex items-center gap-4 text-[10px] text-slate-500">
            <span>{new Date().toLocaleTimeString()}</span>
            <span>Pharmacy: {pharmacyId?.slice(0,8)}…</span>
          </div>
        </header>

        {/* ARIA live region for critical alerts */}
        <div aria-live="assertive" aria-atomic="true" className="sr-only">
          {criticalAlerts > 0 && `Critical security alert: ${criticalAlerts} unresolved`}
        </div>

        {/* Section content — wrapped in ErrorBoundary per section */}
        <main className="flex-1 overflow-y-auto">
          <ErrorBoundary
            key={activeSection}
            label={SECTIONS.find(s => s.id === activeSection)?.label}
          >
            <ActiveComponent />
          </ErrorBoundary>
        </main>
      </div>
    </div>
  )
}
