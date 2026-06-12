/**
 * PharmPilot Dashboard Shell
 * ===========================
 * Main navigation hub connecting all dashboard sections.
 * Left sidebar with section nav. Top bar with live status + AI cost.
 * Keyboard: Alt+number/Alt+K switches sections. Escape returns to workstation.
 */
import { useState, useEffect } from 'react'
import { useQuery } from '@tanstack/react-query'
import {
  Home, Package, Brain, ShieldCheck, DollarSign, Users, Mic, Bot, BookOpen,
  AlertTriangle, Search, Clock, Building2, ArrowLeft, ChevronsLeft, ChevronsRight,
  Stethoscope, Clipboard, ClipboardCheck, Dna, MessageCircle, Send, type LucideIcon,
} from 'lucide-react'
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
import ClinicalAssistant     from './dashboards/ClinicalAssistant'
import ADRDetective          from './dashboards/ADRDetective'
import CounsellingGenerator  from './dashboards/CounsellingGenerator'
import PolypharmacyReview    from './dashboards/PolypharmacyReview'
import Pharmacogenomics      from './dashboards/Pharmacogenomics'
import PhysicianMessageComposer from './dashboards/PhysicianMessageComposer'

const SECTIONS = [
  { id:'command',    key:'1', label:'Command Center',     icon:Home,        description:'Owner overview',      shortcut:'Alt+1' },
  { id:'inventory',  key:'2', label:'Inventory AI',       icon:Package,     description:'ML stock brain',       shortcut:'Alt+2' },
  { id:'clinical',   key:'3', label:'Clinical Intel',     icon:Brain,       description:'Patient safety',       shortcut:'Alt+3' },
  { id:'cds',        key:'4', label:'Clinical Assistant', icon:Stethoscope, description:'CDS alerts',           shortcut:'Alt+4' },
  { id:'adr',        key:'5', label:'ADR Detective',      icon:ClipboardCheck, description:'Side-effect review', shortcut:'Alt+5' },
  { id:'counselling', key:'c', label:'Counselling',       icon:MessageCircle, description:'Patient counselling', shortcut:'Alt+C' },
  { id:'physmsg',    key:'m', label:'Physician Message',  icon:Send,        description:'Prescriber communication', shortcut:'Alt+M' },
  { id:'poly',       key:'6', label:'Polypharmacy',       icon:Clipboard,   description:'Deprescribing review', shortcut:'Alt+6' },
  { id:'pgx',        key:'g', label:'Pharmacogenomics',   icon:Dna,         description:'PGx rules',             shortcut:'Alt+G' },
  { id:'security',   key:'7', label:'Surveillance',       icon:ShieldCheck, description:'Security & safety',    shortcut:'Alt+7' },
  { id:'financial',  key:'8', label:'Financial Ops',      icon:DollarSign,  description:'Revenue & claims',     shortcut:'Alt+8' },
  { id:'adherence',  key:'9', label:'Patient Care',       icon:Users,       description:'Adherence & MTM',      shortcut:'Alt+9' },
  { id:'ai-hub',     key:'0', label:'AI Hub',             icon:Bot,         description:'Multi-AI control',     shortcut:'Alt+0' },
  { id:'knowledge',  key:'k', label:'Knowledge Base',     icon:BookOpen,    description:'Train clinical brain', shortcut:'Alt+K' },
] as const satisfies ReadonlyArray<{ id: string; key: string; label: string; icon: LucideIcon; description: string; shortcut: string }>

type SectionId = typeof SECTIONS[number]['id']

const SECTION_COMPONENTS: Record<SectionId, React.ComponentType> = {
  command:   CommandCenter,
  inventory: InventoryIntelligence,
  clinical:  ClinicalIntelligence,
  cds:       ClinicalAssistant,
  adr:       ADRDetective,
  counselling: CounsellingGenerator,
  physmsg:   PhysicianMessageComposer,
  poly:      PolypharmacyReview,
  pgx:       Pharmacogenomics,
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
  const [collapsed, setCollapsed] = useState(false)
  const [clock, setClock] = useState(() => new Date())
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

  // Keep the top-bar clock fresh without re-rendering the whole shell every second
  useEffect(() => {
    const t = setInterval(() => setClock(new Date()), 30_000)
    return () => clearInterval(t)
  }, [])

  // Responsive sidebar: auto-collapse to the icon rail below the `lg` breakpoint
  // (1024px) so the fixed 224px nav doesn't crowd out content on laptops/tablets.
  // The user's manual Alt+B toggle still works and isn't fought by this — it only
  // adjusts the default in response to *changes* in viewport size.
  useEffect(() => {
    const mql = window.matchMedia('(max-width: 1023px)')
    setCollapsed(mql.matches)
    const onChange = (e: MediaQueryListEvent) => setCollapsed(e.matches)
    mql.addEventListener('change', onChange)
    return () => mql.removeEventListener('change', onChange)
  }, [])

  // Keyboard shortcuts: Alt+1..9/0/K, Escape, Alt+B to toggle sidebar
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      const key = e.key.toLowerCase()
      if (e.altKey && (((key >= '1' && key <= '9') || key === '0' || key === 'k' || key === 'g' || key === 'c' || key === 'm'))) {
        const section = SECTIONS.find(s => s.key === key)
        if (section) setActiveSection(section.id)
        e.preventDefault()
      }
      if (e.altKey && key === 'b') {
        setCollapsed(c => !c)
        e.preventDefault()
      }
      if (e.key === 'Escape' && onExitDashboard) onExitDashboard()
    }
    window.addEventListener('keydown', handler)
    return () => window.removeEventListener('keydown', handler)
  }, [onExitDashboard])

  const ActiveComponent = SECTION_COMPONENTS[activeSection]
  const activeMeta      = SECTIONS.find(s => s.id === activeSection)!
  const criticalAlerts  = summary?.critical_unresolved || 0
  const totalAICost     = aiData?.total_cost_today || 0

  // Section alert badges
  const sectionBadges: Partial<Record<SectionId, { value: string; tone: 'critical' | 'attention' }>> = {
    security: criticalAlerts > 0 ? { value: String(criticalAlerts), tone: 'critical' } : undefined,
    audio:    { value: '3', tone: 'attention' },  // Pending profile updates
    clinical: { value: '2', tone: 'attention' },  // REMS blockers
  }

  return (
    <div className="flex h-screen bg-[#0f1117] text-slate-100 overflow-hidden">

      {/* ── Left Sidebar ──────────────────────────────────────────────── */}
      <nav
        className={`${collapsed ? 'w-[60px]' : 'w-56'} flex-shrink-0 bg-[#0f1117] border-r border-[#1e293b]
                    flex flex-col transition-[width] duration-200 ease-out`}
      >
        {/* Logo */}
        <div className={`flex items-center ${collapsed ? 'justify-center px-0' : 'justify-between px-4'} h-14 border-b border-[#1e293b]`}>
          <div className="flex items-center gap-2.5 min-w-0">
            <div className="w-8 h-8 rounded-lg bg-gradient-to-br from-blue-600 to-blue-800
                            flex items-center justify-center shadow-sm shadow-blue-950/50 flex-shrink-0">
              <span className="text-sm font-black text-white tracking-tight">Rx</span>
            </div>
            {!collapsed && (
              <div className="min-w-0">
                <p className="font-bold text-slate-100 text-sm leading-tight truncate">PharmPilot</p>
                <p className="text-[10px] text-slate-500 leading-tight truncate">Pharmacy OS</p>
              </div>
            )}
          </div>
        </div>

        {/* Section navigation */}
        <div className="flex-1 overflow-y-auto py-3 px-2.5 space-y-0.5">
          {!collapsed && (
            <p className="px-2 pb-1.5 text-[10px] font-semibold uppercase tracking-widest text-slate-600">
              Dashboards
            </p>
          )}
          {SECTIONS.map(section => {
            const isActive = activeSection === section.id
            const badge    = sectionBadges[section.id]
            const Icon     = section.icon
            return (
              <button
                key={section.id}
                onClick={() => setActiveSection(section.id)}
                aria-label={`${section.label} — ${section.description}${badge ? `, ${badge.value} ${badge.tone === 'critical' ? 'critical alerts' : 'pending'}` : ''} (${section.shortcut})`}
                aria-current={isActive ? 'page' : undefined}
                className={`group relative w-full flex items-center gap-2.5 rounded-lg text-left
                           transition-colors duration-150
                           ${collapsed ? 'justify-center px-0 py-2.5' : 'px-2.5 py-2'}
                           ${isActive
                             ? 'bg-blue-600/15 text-blue-300 ring-1 ring-inset ring-blue-500/30'
                             : 'text-slate-400 hover:text-slate-200 hover:bg-white/[0.04]'
                           }`}
                title={`${section.label} — ${section.description} (${section.shortcut})`}
              >
                {isActive && (
                  <span className="absolute left-0 top-1/2 -translate-y-1/2 h-5 w-[3px] rounded-r-full bg-blue-400" />
                )}
                <Icon size={17} strokeWidth={isActive ? 2.25 : 1.75} className="flex-shrink-0" />
                {!collapsed && (
                  <span className="text-[13px] font-medium leading-tight truncate flex-1">{section.label}</span>
                )}
                {badge && (
                  <span
                    className={`flex-shrink-0 text-[10px] font-bold leading-none rounded-full
                               ${collapsed ? 'absolute top-1 right-1 w-2 h-2 p-0' : 'px-1.5 py-0.5'}
                               ${badge.tone === 'critical' ? 'bg-red-500 text-white' : 'bg-amber-500 text-amber-950'}`}
                  >
                    {collapsed ? '' : badge.value}
                  </span>
                )}
              </button>
            )
          })}
        </div>

        {/* Footer */}
        <div className={`border-t border-[#1e293b] ${collapsed ? 'px-1.5 py-2' : 'px-3 py-3'} space-y-2`}>
          {!collapsed && (
            <div className="flex items-center justify-between rounded-lg bg-white/[0.03] px-2.5 py-1.5">
              <span className="text-[10px] font-medium text-slate-500">AI spend today</span>
              <span className="text-[11px] font-mono font-semibold text-slate-300 tabular-nums">${totalAICost.toFixed(3)}</span>
            </div>
          )}
          <button
            onClick={() => setCollapsed(c => !c)}
            aria-expanded={!collapsed}
            aria-label={collapsed ? 'Expand sidebar' : 'Collapse sidebar'}
            className="w-full flex items-center justify-center gap-2 rounded-lg py-1.5
                       text-slate-500 hover:text-slate-300 hover:bg-white/[0.04] transition-colors"
            title={collapsed ? 'Expand sidebar (Alt+B)' : 'Collapse sidebar (Alt+B)'}
          >
            {collapsed ? <ChevronsRight size={15} /> : <ChevronsLeft size={15} />}
            {!collapsed && <span className="text-[11px] font-medium">Collapse</span>}
          </button>
          {!collapsed && (
            <p className="text-[10px] text-slate-600 capitalize truncate px-1">{userRole.replace(/_/g, ' ') || 'Staff'}</p>
          )}
          {onExitDashboard && (
            <button onClick={onExitDashboard}
              className={`w-full flex items-center gap-2 text-slate-500 hover:text-slate-200
                         hover:bg-white/[0.04] rounded-lg py-1.5 transition-colors
                         ${collapsed ? 'justify-center' : 'px-2'}`}
              title="Return to workstation (Esc)"
            >
              <ArrowLeft size={14} />
              {!collapsed && <span className="text-[11px] font-medium">Workstation</span>}
            </button>
          )}
        </div>
      </nav>

      {/* ── Main content ──────────────────────────────────────────────── */}
      <div className="flex-1 flex flex-col overflow-hidden min-w-0">
        {/* Top bar */}
        <header className="flex-shrink-0 h-14 bg-[#0f1117]/80 backdrop-blur border-b border-[#1e293b]
                           flex items-center justify-between gap-4 px-6">
          <div className="flex items-center gap-3 min-w-0">
            <div className="min-w-0">
              {/* Section title — intentionally not an <h1>: each dashboard renders its own
                  canonical <h1>, so this is a secondary/contextual label (role=heading level 2). */}
              <p role="heading" aria-level={2} className="text-[15px] font-semibold text-slate-100 leading-tight truncate">{activeMeta.label}</p>
              <p className="text-[11px] text-slate-500 leading-tight truncate">{activeMeta.description}</p>
            </div>
            {criticalAlerts > 0 && (
              <span className="hidden sm:inline-flex items-center gap-1.5 bg-red-500/15 text-red-300
                               ring-1 ring-inset ring-red-500/30 px-2.5 py-1 rounded-full text-[11px] font-semibold">
                <AlertTriangle size={12} className="animate-pulse" />
                {criticalAlerts} critical alert{criticalAlerts === 1 ? '' : 's'}
              </span>
            )}
          </div>

          <div className="flex items-center gap-2">
            {/* Search — not yet wired to a real index, so it's presented as a clearly
                inert placeholder (no fake keyboard-shortcut hint, default cursor, reduced
                contrast) rather than an affordance that looks live but does nothing. */}
            <div
              aria-hidden="true"
              title="Search is coming soon"
              className="hidden md:flex items-center gap-2 rounded-lg bg-white/[0.02] border border-white/[0.05]
                         px-3 py-1.5 text-slate-600 w-56 lg:w-72 cursor-default select-none"
            >
              <Search size={14} className="flex-shrink-0 opacity-60" />
              <span className="text-[12px] truncate opacity-60">Search (coming soon)</span>
            </div>

            <div className="hidden lg:flex items-center gap-1.5 text-[11px] text-slate-500 px-2.5 py-1.5
                            rounded-lg bg-white/[0.03] border border-white/[0.06]">
              <Clock size={13} />
              <span className="tabular-nums">{clock.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}</span>
            </div>
            <div className="hidden lg:flex items-center gap-1.5 text-[11px] text-slate-500 px-2.5 py-1.5
                            rounded-lg bg-white/[0.03] border border-white/[0.06]">
              <Building2 size={13} />
              <span className="font-mono truncate max-w-[7rem]">{pharmacyId ? `${pharmacyId.slice(0, 8)}…` : 'No pharmacy'}</span>
            </div>
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
            label={activeMeta.label}
          >
            <ActiveComponent />
          </ErrorBoundary>
        </main>
      </div>
    </div>
  )
}
