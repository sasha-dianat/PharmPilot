/**
 * PharmPilot AI — Design System Token Definitions
 * ================================================
 * Single source of truth for all 3 surfaces:
 *   Workstation (clinical dark), Admin Portal (executive light), Patient App (mobile)
 *
 * PHILOSOPHY: Color encodes meaning. Zero decorative color.
 * Every token answers: "Why does this look like this?"
 */

// ── Severity (universal across all clinical surfaces) ─────────────────────
export const severity = {
  critical:      { bg: '#450a0a', border: '#ef4444', text: '#fca5a5', dot: '#ef4444' },
  high:          { bg: '#431407', border: '#f97316', text: '#fdba74', dot: '#f97316' },
  caution:       { bg: '#422006', border: '#eab308', text: '#fde047', dot: '#eab308' },
  safe:          { bg: '#052e16', border: '#22c55e', text: '#86efac', dot: '#22c55e' },
  informational: { bg: '#1e293b', border: '#64748b', text: '#94a3b8', dot: '#64748b' },
} as const

// ── Clinical Workstation Theme (dark, high-contrast) ──────────────────────
export const clinical = {
  // Backgrounds
  bg: {
    base:      '#0f1117',   // Base canvas — near-black, not pure black
    surface:   '#1a1f2e',   // Cards, panels
    elevated:  '#242938',   // Modals, dropdowns
    hover:     '#2d3348',   // Interactive hover state
    active:    '#1d4ed8',   // Selected/active item
  },
  // Text
  text: {
    primary:   '#f8fafc',   // Main content
    secondary: '#94a3b8',   // Labels, metadata
    muted:     '#475569',   // Placeholder, disabled
    inverse:   '#0f1117',   // Text on bright backgrounds
  },
  // Borders
  border: {
    subtle:    '#1e293b',   // Card borders
    default:   '#334155',   // Input borders
    strong:    '#475569',   // Focused/active borders
  },
  // Actions
  action: {
    primary:   '#3b82f6',   // Primary CTA — electric blue
    hover:     '#2563eb',   // Primary hover
    secondary: '#1e3a5f',   // Secondary action
  },
} as const

// ── Owner Dashboard Theme (light, executive) ──────────────────────────────
export const executive = {
  bg: {
    base:      '#ffffff',
    surface:   '#f8fafc',
    elevated:  '#f1f5f9',
    hover:     '#e2e8f0',
  },
  text: {
    primary:   '#0f172a',
    secondary: '#475569',
    muted:     '#94a3b8',
  },
  border: {
    subtle:    '#f1f5f9',
    default:   '#e2e8f0',
    strong:    '#cbd5e1',
  },
  action: {
    primary:   '#3b82f6',
    hover:     '#2563eb',
  },
} as const

// ── Typography scale ───────────────────────────────────────────────────────
export const typography = {
  // KPI numbers — big, bold, immediate
  kpi: 'text-4xl font-bold tabular-nums tracking-tight',
  kpiLg: 'text-5xl font-black tabular-nums tracking-tighter',

  // Section headings
  h1: 'text-2xl font-bold text-slate-100',
  h2: 'text-lg font-semibold text-slate-200',
  h3: 'text-base font-semibold text-slate-300',

  // Labels (uppercase small caps style)
  label: 'text-xs font-semibold uppercase tracking-widest text-slate-500',

  // Drug names — monospace to distinguish from prose
  drugName: 'font-mono text-sm font-medium',
  ndc:      'font-mono text-xs text-slate-400',

  // Body
  body:  'text-sm text-slate-300',
  small: 'text-xs text-slate-400',

  // Alerts
  alertCritical: 'text-sm font-semibold text-red-300',
  alertHigh:     'text-sm font-semibold text-orange-300',
} as const

// ── Chart color palettes (colorblind-safe) ────────────────────────────────
export const chartPalettes = {
  // Sequential (data intensity — single variable)
  sequential: ['#eff6ff','#bfdbfe','#93c5fd','#60a5fa','#3b82f6','#2563eb','#1d4ed8'],
  sequentialRed: ['#fef2f2','#fecaca','#f87171','#ef4444','#dc2626','#b91c1c','#7f1d1d'],

  // Diverging (good vs bad — PDC scores, budget vs actual)
  diverging: ['#dc2626','#f97316','#eab308','#94a3b8','#22c55e','#16a34a','#15803d'],

  // Categorical (multiple independent series)
  categorical: ['#3b82f6','#f97316','#22c55e','#a855f7','#06b6d4','#f59e0b','#64748b','#ec4899'],

  // Severity-ordered (use for alert type distributions)
  severity: ['#ef4444','#f97316','#eab308','#22c55e','#64748b'],

  // AI provider colors (consistent brand)
  aiProviders: {
    anthropic:  '#d97706',  // Amber — Claude warmth
    openai:     '#22c55e',  // Green — OpenAI
    google:     '#3b82f6',  // Blue — Google
    cohere:     '#a855f7',  // Purple — Cohere
    mistral:    '#06b6d4',  // Cyan — Mistral
    groq:       '#f97316',  // Orange — Groq speed
    ollama:     '#64748b',  // Gray — local/offline
  },
} as const

// ── Animation durations (clinical-appropriate) ────────────────────────────
export const animation = {
  instant:  0,
  fast:     150,   // Status changes, hover
  normal:   300,   // Chart data updates
  slow:     500,   // Page transitions
  alertIn:  200,   // Alert card arrival (fade-in only)
  // NO infinite animations on clinical screens
} as const

// ── Spacing (consistent grid) ──────────────────────────────────────────────
export const spacing = {
  panelGap:  'gap-4',      // Between dashboard panels
  cardPad:   'p-4',        // Standard card padding
  cardPadLg: 'p-6',        // Large card padding
  sectionGap:'gap-6',      // Between dashboard sections
} as const

// ── Component inventory ────────────────────────────────────────────────────
export const COMPONENT_INVENTORY = [
  // Charts
  'KPICard',              // Single metric with sparkline + trend
  'PharmacyHealthScore',  // Composite score gauge
  'RevenuePulseChart',    // Recharts AreaChart with 3 series
  'RxQueueHeatmap',       // Nivo HeatMap (24h × 7d)
  'ClaimWaterfallChart',  // Recharts BarChart waterfall pattern
  'AlertSeverityRing',    // Recharts PieChart donut
  'DemandForecastPanel',  // Recharts ComposedChart (bar+line+CI)
  'StockHealthMatrix',    // Custom grid with color cells
  'ExpiryTimeline',       // Nivo Bar (Gantt style)
  'DrugInteractionGraph', // react-force-graph-2d
  'AdherenceCohortScatter','Recharts ScatterChart',
  'PharmacyFloorMap',     // Custom SVG with WebSocket person positions
  'BehavioralTimeline',   // Vertical event timeline
  'RevenueWaterfallChart','Recharts BarChart waterfall',
  'CMSStarGauge',         // Radial gauge per metric
  'AIProviderGrid',       // Provider status cards
  'ModelRoutingTable',    // Drag-and-drop routing config
  'AIPerformanceScatter', // Cost vs quality scatter
  'LiveTranscriptFeed',   // Real-time SSE text panel
  'MTMKanbanBoard',       // Kanban pipeline
  'AdherenceFunnel',      // Nivo Funnel
  // Utility
  'SkeletonCard',         // Loading skeleton
  'EmptyState',           // Empty state with CTA
  'ErrorBoundaryPanel',   // Error display with retry
  'SeverityBadge',        // Icon + text severity indicator
  'LiveDot',              // Pulsing status indicator
] as const
