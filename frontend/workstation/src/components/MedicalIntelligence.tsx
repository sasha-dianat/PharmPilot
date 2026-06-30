/**
 * MedicalIntelligence — one clinical console for the pharmacist.
 * ==============================================================
 * Consolidates the previously-scattered review surfaces into a single,
 * severity-ranked, low-noise console:
 *
 *   • Drug interactions       (deterministic engine — interaction-report)
 *   • Adverse reactions / DUR (rxApi.durAlerts — duplicate therapy, allergy…)
 *   • Specialist council      (precomputed analysis.council_cache)
 *   • Patient trajectory       (embedded TrajectorySignalStrip — labs, care gaps)
 *
 * Design — "editorial-clinical": a calm masthead with serif severity numerals
 * gives the whole picture at a glance; a single severity-ranked feed sits below,
 * each finding tagged with its source. Collapsed rows stay scannable; one click
 * opens a full clinical dossier — mechanism, evidence, predicted magnitude /
 * onset, patient-specific factors and a confidence meter — so the richness is
 * there on demand without ever burying a blocker.
 *
 * Prescriber context now lives on the right (PatientPanel), next to the
 * prescriber's council ID — this console is patient/medication-centric.
 *
 * Safety unchanged: this only PRESENTS. The DUR hard-stop gate and the serious-
 * interaction acknowledgment gate stay in VerificationCenter; we emit
 * `onSerious(findings_hash)` and route DUR overrides through rxApi.overrideDur.
 */
import { useEffect, useMemo, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { clinicalApi, rxApi } from '../lib/api'
import { toSeverity, type Severity } from '../design/severity'
import type { DURAlert, AlertSeverity } from '../stores/rxQueue'
import TrajectorySignalStrip from './TrajectorySignalStrip'
import PhysicianLetterModal from './PhysicianLetterModal'
import DictateNote from './DictateNote'

// ── External shapes (mirror the engine payload) ───────────────────────────────
interface Participant { name: string; kind: string; provenance?: string; last_seen?: string | null }
interface Finding {
  rule_id: string; type: string; severity: string; base_severity?: string; direction: string
  predicted_magnitude: string | null; onset_offset?: string | null
  mechanism: string; mechanism_basis?: string | null; clinical_problem?: string
  suggested_actions: string[]; evidence_sources?: string[]; evidence_grade: string
  source: string; patient_specific_factors?: string[]; confidence?: number
  recency_note: string | null; section?: 'dispense' | 'profile'; participants: Participant[]
}
interface ReportPayload {
  report: { summary: Record<string, number>; degraded: boolean; findings: Finding[] }
  findings_hash: string; cached: boolean; computed_at: string
}
interface CouncilFinding {
  specialist: string; severity: string; message: string
  drug_name?: string; evidence_source?: string; evidence_grade?: string
}
interface CouncilCache {
  generated_at: string; specialists_consulted: string[]; summary: string
  blockers: CouncilFinding[]; cautions: CouncilFinding[]; counseling: CouncilFinding[]
  monitoring: CouncilFinding[]; clarification: CouncilFinding[]; hereditary: CouncilFinding[]
}
interface RxAnalysis {
  status: string; triage_lane: 'green' | 'amber' | 'red' | null
  council_cache: CouncilCache | null; council_computed_at: string | null
}

// ── Severity styling — Clinical Daylight tokens (light theme) ─────────────────
const SEV: Record<Severity, { text: string; bg: string; border: string; ring: string; bar: string }> = {
  blocker: { text: 'text-blocker', bg: 'bg-blocker-soft', border: 'border-blocker/30', ring: 'ring-blocker/20', bar: 'bg-blocker' },
  caution: { text: 'text-caution', bg: 'bg-caution-soft', border: 'border-caution/30', ring: 'ring-caution/20', bar: 'bg-caution' },
  warning: { text: 'text-warning', bg: 'bg-warning-soft', border: 'border-warning/30', ring: 'ring-warning/20', bar: 'bg-warning' },
  counsel: { text: 'text-counsel', bg: 'bg-counsel-soft', border: 'border-counsel/30', ring: 'ring-counsel/20', bar: 'bg-counsel' },
  intel:   { text: 'text-intel',   bg: 'bg-intel-soft',   border: 'border-intel/30',   ring: 'ring-intel/20',   bar: 'bg-intel' },
  safe:    { text: 'text-safe',    bg: 'bg-safe-soft',    border: 'border-safe/30',    ring: 'ring-safe/20',    bar: 'bg-safe' },
  dur:     { text: 'text-dur',     bg: 'bg-dur-soft',     border: 'border-dur/30',     ring: 'ring-dur/20',     bar: 'bg-dur' },
  neutral: { text: 'text-ink3',    bg: 'bg-surface2',     border: 'border-line2',      ring: 'ring-line2',      bar: 'bg-line2' },
}
const RANK: Severity[] = ['blocker', 'caution', 'warning', 'counsel', 'intel', 'dur', 'safe', 'neutral']
const isSerious = (s: Severity) => s === 'blocker' || s === 'caution'
const norm = (s: string) => s.toLowerCase().replace(/[^a-z0-9 ]/g, ' ').trim()
const humanize = (s: string) => s.replace(/_/g, ' ').replace(/\b\w/g, c => c.toUpperCase())
const DUR_SEV: Record<AlertSeverity, Severity> = {
  critical: 'blocker', high: 'caution', moderate: 'warning', informational: 'intel',
}

// ── Interaction classification (mechanism · category · likely consequence) ────
function ixCategory(f: Finding): string {
  const t = f.type || ''
  const m = `${f.mechanism_basis || ''} ${f.mechanism || ''}`.toLowerCase()
  if (t === 'duplicate_therapy') return 'Therapeutic duplication'
  if (t === 'drug_allergy') return 'Allergy / cross-reactivity'
  if (t === 'drug_disease') return 'Drug–disease'
  if (t === 'drug_context') return 'Clinical / monitoring'
  if (/cyp|metaboli|transport|p-?gp|oatp|absorption|chelat|renal|clearance|induc|inhibit/.test(m))
    return 'Pharmacokinetic (PK)'
  if (/additive|serotonin|\bqt\b|bleed|sedat|\bcns\b|hyperkal|hypotens|oppos|antagon|synerg/.test(m))
    return 'Pharmacodynamic (PD)'
  return 'Drug–drug'
}

function ixConsequence(f: Finding): string {
  const dir = (f.direction || '').toLowerCase()
  const sev = f.severity === 'Contraindicated' ? 'serious harm — avoid the combination'
    : f.severity === 'Major' ? 'clinically significant adverse effect likely'
    : f.severity === 'Moderate' ? 'monitor for an adverse effect'
    : 'minor effect possible'
  const d = dir.includes('toxic') ? 'higher drug levels / toxicity'
    : dir.includes('efficacy') ? 'loss of efficacy'
    : dir.includes('additive') ? 'additive effect'
    : dir.includes('oppos') ? 'opposing / reduced effect' : ''
  const mag = f.predicted_magnitude ? ` · ${f.predicted_magnitude}` : ''
  return `${[d, sev].filter(Boolean).join(' — ')}${mag}`
}

// ── Unified feed item ─────────────────────────────────────────────────────────
type Source = 'interaction' | 'adr' | 'council'
interface Detail {
  clinicalProblem?: string; mechanismBasis?: string | null
  predictedMagnitude?: string | null; onset?: string | null; direction?: string
  confidence?: number; recency?: string | null
  evidenceSources?: string[]; patientFactors?: string[]
  participants?: Participant[]
  specialist?: string; evidenceSource?: string
  alertType?: string; interactingDrug?: string
  category?: string; consequence?: string
}
interface FeedItem {
  key: string; sev: Severity; sevLabel: string; source: Source; sourceLabel: string
  group: string; title: string; detail: string; action?: string; grade?: string
  predicted?: boolean; durAlert?: DURAlert; rich: Detail
}

export default function MedicalIntelligence({
  patientId, rxId, drugName, durAlerts, analysis, onSerious, onAlertResolved,
}: {
  patientId?: string
  rxId: string
  drugName: string
  durAlerts: DURAlert[]
  analysis?: RxAnalysis
  onSerious: (hash: string | null) => void
  onAlertResolved: () => void
}) {
  const [filter, setFilter] = useState<'all' | Source>('all')
  const [showRoutine, setShowRoutine] = useState(false)
  const [openKey, setOpenKey] = useState<string | null>(null)
  const [letterOpen, setLetterOpen] = useState(false)
  const [overrideReason, setOverrideReason] = useState('')
  const [overrideKey, setOverrideKey] = useState<string | null>(null)
  const [dismissed, setDismissed] = useState<Set<string>>(new Set())
  const [savingOverride, setSavingOverride] = useState(false)

  const { data: rpt } = useQuery<ReportPayload>({
    queryKey: ['interaction-report', patientId],
    enabled: !!patientId,
    queryFn: () => clinicalApi.getInteractionReport(patientId!).then(r => r.data),
  })
  const report = rpt?.report
  const findings = useMemo(() => report?.findings ?? [], [report])
  const findingsHash = rpt?.findings_hash ?? null

  // ── Build the unified, de-duplicated feed ──────────────────────────────────
  const items = useMemo<FeedItem[]>(() => {
    const out: FeedItem[] = []
    const interactionTitles: string[] = []

    findings.forEach((f, i) => {
      const sev = toSeverity(f.severity)
      const title = f.participants.map(p => p.name).join(' × ')
      interactionTitles.push(norm(title))
      out.push({
        key: `ix-${i}`, sev, sevLabel: f.severity, source: 'interaction', sourceLabel: 'Interaction',
        group: f.section === 'profile' ? 'Existing patient-profile' : 'Introduced by this prescription',
        title, detail: f.mechanism, action: f.suggested_actions?.[0],
        grade: f.evidence_grade, predicted: f.source === 'inferred_mechanistic',
        rich: {
          clinicalProblem: f.clinical_problem, mechanismBasis: f.mechanism_basis,
          predictedMagnitude: f.predicted_magnitude, onset: f.onset_offset, direction: f.direction,
          confidence: f.confidence, recency: f.recency_note,
          evidenceSources: f.evidence_sources, patientFactors: f.patient_specific_factors,
          participants: f.participants,
          category: ixCategory(f), consequence: ixConsequence(f),
        },
      })
    })

    durAlerts
      .filter(a => !a.was_overridden && !dismissed.has(a.id))
      .filter(a => {
        const isInteraction = /interact/i.test(a.alert_type) || !!a.interacting_drug_name
        if (!isInteraction) return true
        const drug = norm(a.interacting_drug_name ?? '')
        return !(drug && interactionTitles.some(t => t.includes(drug)))
      })
      .forEach(a => {
        const sev = DUR_SEV[a.severity] ?? 'intel'
        out.push({
          key: `dur-${a.id}`, sev, sevLabel: humanize(a.severity), source: 'adr', sourceLabel: 'ADR / DUR',
          group: 'Adverse reactions & utilization',
          title: humanize(a.alert_type) + (a.interacting_drug_name ? ` · ${a.interacting_drug_name}` : ''),
          detail: a.description, grade: a.evidence_grade, durAlert: a,
          rich: { alertType: humanize(a.alert_type), interactingDrug: a.interacting_drug_name },
        })
      })

    const c = analysis?.council_cache
    if (c) {
      const cats: [CouncilFinding[], string, string][] = [
        [c.blockers, 'blocker', 'blk'], [c.cautions, 'caution', 'cau'], [c.hereditary, 'counsel', 'her'],
        [c.clarification, 'intel', 'clr'], [c.counseling, 'counsel', 'cns'], [c.monitoring, 'warning', 'mon'],
      ]
      cats.forEach(([list, fallback, catId]) => {
        (list ?? []).forEach((f, i) => {
          const sev = f.severity ? toSeverity(f.severity) : (fallback as Severity)
          out.push({
            key: `co-${catId}-${i}`, sev,
            sevLabel: humanize(f.severity || fallback), source: 'council',
            sourceLabel: `Council · ${f.specialist}`, group: 'Specialist council',
            title: f.drug_name ? `${f.specialist} — ${f.drug_name}` : f.specialist,
            detail: f.message, grade: f.evidence_grade,
            rich: { specialist: f.specialist, evidenceSource: f.evidence_source },
          })
        })
      })
    }
    return out.sort((a, b) => RANK.indexOf(a.sev) - RANK.indexOf(b.sev))
  }, [findings, durAlerts, analysis, dismissed])

  // ── Masthead tally ─────────────────────────────────────────────────────────
  const tally = useMemo(() => {
    const t = { blocker: 0, caution: 0, watch: 0, note: 0 }
    items.forEach(i => {
      if (i.sev === 'blocker') t.blocker++
      else if (i.sev === 'caution') t.caution++
      else if (i.sev === 'warning' || i.sev === 'dur') t.watch++
      else t.note++
    })
    return t
  }, [items])

  const seriousInteractions = findings.some(f => isSerious(toSeverity(f.severity)))
  useEffect(() => {
    if (report && !report.degraded) onSerious(seriousInteractions ? findingsHash : null)
    else onSerious(null)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [rpt, report?.degraded, seriousInteractions, findingsHash])

  const contraindicated = findings.filter(f => f.severity === 'Contraindicated')
  const hardStops = durAlerts.filter(a => a.is_hard_stop && !a.was_overridden).length
  const counts = {
    all: items.length,
    interaction: items.filter(i => i.source === 'interaction').length,
    adr: items.filter(i => i.source === 'adr').length,
    council: items.filter(i => i.source === 'council').length,
  }
  const filtered = filter === 'all' ? items : items.filter(i => i.source === filter)
  const serious = filtered.filter(i => isSerious(i.sev))
  const routine = filtered.filter(i => !isSerious(i.sev))
  const shown = showRoutine ? filtered : serious
  const grouped = useMemo(() => {
    const map = new Map<string, FeedItem[]>()
    shown.forEach(i => { (map.get(i.group) ?? map.set(i.group, []).get(i.group)!).push(i) })
    return [...map.entries()]
  }, [shown])

  const doOverride = async (a: DURAlert) => {
    if (overrideReason.trim().length < 10) return
    setSavingOverride(true)
    try {
      await rxApi.overrideDur(rxId, a.id, overrideReason)
      setOverrideKey(null); setOverrideReason(''); onAlertResolved()
    } catch (e) { console.error('Override failed:', e) }
    finally { setSavingOverride(false) }
  }

  if (!patientId) return null
  const computedAt = rpt?.computed_at ? new Date(rpt.computed_at) : null
  const summary = analysis?.council_cache?.summary

  return (
    <div className="cd-card cd-section overflow-hidden">
      {/* ── Masthead ───────────────────────────────────────────────────────── */}
      <div className="px-4 pt-3.5 pb-3 border-b border-line bg-gradient-to-b from-surface to-surface2/40">
        <div className="flex items-center gap-2">
          <span className="w-6 h-6 rounded-lg bg-intel-soft text-intel flex items-center justify-center text-sm">🩺</span>
          <h3 className="cd-ui text-[15px] font-semibold text-ink tracking-tight">Medical Intelligence</h3>
          <span className="cd-ui text-[10px] text-ink3 hidden sm:inline">interactions · adverse reactions · council · trajectory</span>
          <span className="ml-auto flex items-center gap-2 text-[10px] text-ink3 cd-data">
            {rpt && <span>{rpt.cached ? 'cached' : 'fresh'}</span>}
            {computedAt && <span>· {computedAt.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}</span>}
          </span>
        </div>

        <div className="flex items-end gap-5 mt-3">
          <Numeral n={tally.blocker} label="Blockers" sev="blocker" />
          <Divider />
          <Numeral n={tally.caution} label="Major" sev="caution" />
          <Divider />
          <Numeral n={tally.watch} label="Watch" sev="warning" />
          <Divider />
          <Numeral n={tally.note} label="Notes" sev="neutral" />
          {analysis?.triage_lane && (
            <span className={`ml-auto self-center cd-ui text-[11px] font-semibold px-2.5 py-1 rounded-full border ${
              analysis.triage_lane === 'green' ? 'bg-safe-soft text-safe border-safe/30'
              : analysis.triage_lane === 'amber' ? 'bg-warning-soft text-warning border-warning/30'
              : 'bg-blocker-soft text-blocker border-blocker/40'}`}>
              {analysis.triage_lane === 'green' ? '🟢 Fast lane'
                : analysis.triage_lane === 'amber' ? '🟡 Standard review' : '🔴 Full scrutiny'}
            </span>
          )}
        </div>

        {summary && (
          <p className="cd-narr text-[13px] text-ink2 leading-relaxed mt-2.5 border-l-2 border-intel/30 pl-2.5">
            {summary}
          </p>
        )}
      </div>

      {/* ── Action-required strip ──────────────────────────────────────────── */}
      {(hardStops > 0 || seriousInteractions) && (
        <div className="flex items-center gap-2 px-4 py-2 bg-blocker-soft border-b border-blocker/20">
          <span className="text-blocker text-sm animate-pulse">●</span>
          <span className="cd-ui text-[12px] font-semibold text-blocker">
            {[hardStops > 0 && `${hardStops} DUR hard stop${hardStops > 1 ? 's' : ''}`,
              seriousInteractions && 'serious interaction sign-off'].filter(Boolean).join(' · ')}
          </span>
          <span className="cd-ui text-[11px] text-blocker/80 ml-auto">Resolve before dispensing</span>
        </div>
      )}
      {report?.degraded && (
        <div className="px-4 py-2 bg-warning-soft border-b border-warning/20 cd-ui text-[12px] text-warning">
          Interaction engine degraded — manual review required.
        </div>
      )}

      {/* ── Filter ─────────────────────────────────────────────────────────── */}
      <div className="flex items-center gap-1 px-3 pt-2.5 pb-1">
        <Seg active={filter === 'all'} onClick={() => setFilter('all')} label="All" n={counts.all} />
        <Seg active={filter === 'interaction'} onClick={() => setFilter('interaction')} label="Interactions" n={counts.interaction} />
        <Seg active={filter === 'adr'} onClick={() => setFilter('adr')} label="Adverse" n={counts.adr} />
        <Seg active={filter === 'council'} onClick={() => setFilter('council')} label="Council" n={counts.council} />
      </div>

      {/* ── Feed ───────────────────────────────────────────────────────────── */}
      <div className="px-3 pb-3 space-y-3 max-h-[30rem] overflow-y-auto">
        {items.length === 0 && (
          <p className="cd-narr text-sm text-safe px-1 py-2">✓ No interactions, adverse-reaction alerts, or council findings for this basket.</p>
        )}

        {grouped.map(([group, list]) => (
          <div key={group} className="space-y-1.5">
            <p className="cd-ui text-[10px] font-semibold uppercase tracking-wider text-ink3 px-1 pt-1">{group}</p>
            {list.map((it, i) => {
              const tok = SEV[it.sev]
              const open = openKey === it.key
              return (
                <div key={it.key}
                  className={`cd-stream cd-hover rounded-lg border ${open ? `${tok.bg} ${tok.border}` : 'border-line bg-surface'}`}
                  style={{ '--i': i } as React.CSSProperties}>
                  {/* clickable header */}
                  <button onClick={() => setOpenKey(open ? null : it.key)}
                    className="w-full text-left flex items-stretch gap-2.5 p-2.5">
                    <span className={`w-1 rounded-full flex-shrink-0 ${tok.bar}`} />
                    <div className="flex-1 min-w-0">
                      <div className="flex items-center gap-1.5 flex-wrap">
                        <span className={`cd-ui text-[11px] font-bold ${tok.text}`}>{it.sevLabel}</span>
                        <span className="cd-data text-[9px] uppercase tracking-wide text-ink3 bg-surface2 border border-line rounded px-1 py-px">{it.sourceLabel}</span>
                        {it.predicted
                          ? <span className="cd-ui text-[10px] text-ink3 italic">predicted</span>
                          : it.grade && <span className="cd-ui text-[10px] text-ink3">{it.grade}</span>}
                        {it.durAlert?.is_hard_stop && (
                          <span className="cd-ui text-[9px] font-bold text-blocker bg-blocker-soft border border-blocker/30 rounded px-1">HARD STOP</span>
                        )}
                      </div>
                      <p className="cd-ui text-[13px] font-bold text-ink leading-snug mt-0.5">{it.title}</p>
                      {!open && <p className="cd-narr text-[12px] text-ink2 leading-snug mt-0.5 line-clamp-1">{it.detail}</p>}
                    </div>
                    <span className={`cd-ui text-ink3 text-[10px] self-center transition-transform ${open ? 'rotate-180' : ''}`}>▾</span>
                  </button>

                  {/* ── Expanded dossier ─────────────────────────────────────── */}
                  {open && (
                    <div className="px-3 pb-3 pt-0.5 space-y-2.5 cd-section">
                      <p className="cd-narr text-[13px] text-ink leading-[1.7]">{it.rich.clinicalProblem || it.detail}</p>
                      {it.rich.category && (
                        <div className="flex flex-wrap items-center gap-2">
                          <span className="text-[9px] uppercase tracking-wide text-ink3">Category</span>
                          <span className="cd-data text-[11px] bg-surface2 border border-line rounded px-1.5 py-0.5 text-ink2">{it.rich.category}</span>
                        </div>
                      )}
                      {it.rich.mechanismBasis && (
                        <Field label="Mechanism">{it.rich.mechanismBasis}</Field>
                      )}
                      {it.rich.consequence && (
                        <div className={`rounded-lg px-2.5 py-1.5 border ${tok.bg} ${tok.border}`}>
                          <div className="text-[9px] uppercase tracking-wide text-ink3">Possible serious result</div>
                          <p className={`cd-narr text-[12.5px] font-medium ${tok.text} leading-snug mt-0.5`}>{it.rich.consequence}</p>
                        </div>
                      )}

                      {/* metric grid */}
                      {(it.rich.direction || it.rich.predictedMagnitude || it.rich.onset || it.rich.confidence != null) && (
                        <div className="grid grid-cols-2 gap-2">
                          {it.rich.direction && <Metric label="Direction" value={humanize(it.rich.direction)} />}
                          {it.rich.predictedMagnitude && <Metric label="Predicted effect" value={it.rich.predictedMagnitude} />}
                          {it.rich.onset && <Metric label="Onset" value={it.rich.onset} />}
                          {it.rich.confidence != null && (
                            <div className="cd-inset px-2 py-1.5">
                              <div className="text-[9px] uppercase tracking-wide text-ink3">Confidence</div>
                              <div className="flex items-center gap-1.5 mt-1">
                                <div className="h-1.5 flex-1 rounded-full bg-line2 overflow-hidden">
                                  <div className={`h-full ${tok.bar}`} style={{ width: `${Math.round(it.rich.confidence * 100)}%` }} />
                                </div>
                                <span className="cd-data text-[10px] text-ink2">{Math.round(it.rich.confidence * 100)}%</span>
                              </div>
                            </div>
                          )}
                        </div>
                      )}

                      {it.rich.patientFactors && it.rich.patientFactors.length > 0 && (
                        <ChipRow label="Patient-specific" items={it.rich.patientFactors} tone="caution" />
                      )}
                      {it.rich.participants && it.rich.participants.length > 0 && (
                        <div>
                          <div className="text-[9px] uppercase tracking-wide text-ink3 mb-1">Involved</div>
                          <div className="flex flex-wrap gap-1">
                            {it.rich.participants.map((p, k) => (
                              <span key={k} className="cd-data text-[10px] bg-surface2 border border-line rounded px-1.5 py-0.5 text-ink2">
                                {p.name}{p.provenance && p.provenance !== 'current_rx' && <span className="text-ink3"> · {p.provenance}</span>}
                              </span>
                            ))}
                          </div>
                        </div>
                      )}
                      {(it.rich.evidenceSources?.length || it.rich.evidenceSource) && (
                        <ChipRow label="Evidence"
                          items={it.rich.evidenceSources?.length ? it.rich.evidenceSources : [it.rich.evidenceSource!]}
                          tone="intel" />
                      )}
                      {it.action && (
                        <div className="bg-intel-soft border border-intel/20 rounded-lg px-2.5 py-1.5">
                          <span className="cd-ui text-[12px] text-intel font-medium">→ {it.action}</span>
                        </div>
                      )}
                      {it.rich.recency && <p className="cd-ui text-[11px] italic text-ink3">{it.rich.recency}</p>}

                      {/* ADR override (high / moderate) — same flow as DURAlertPanel */}
                      {it.durAlert && it.durAlert.severity !== 'critical' && it.durAlert.severity !== 'informational' && (
                        overrideKey === it.key ? (
                          <div className="space-y-1.5 pt-1.5 border-t border-line">
                            <div className="flex items-center justify-between">
                              <label className="cd-ui text-[11px] font-medium text-ink2">Override reason (min 10 chars)</label>
                              <DictateNote context="dur_override" language="fa" compact
                                onConfirm={(t) => setOverrideReason(prev => prev ? `${prev} ${t}` : t)} />
                            </div>
                            <textarea autoFocus value={overrideReason} rows={2}
                              onChange={e => setOverrideReason(e.target.value)}
                              placeholder="Document clinical reasoning (or tap 🎙)…"
                              className="cd-ui w-full text-[12px] bg-surface border border-line2 rounded-lg p-2 resize-none focus:outline-none focus:ring-2 focus:ring-caution/30" />
                            <div className="flex gap-1.5 items-center">
                              <button onClick={() => doOverride(it.durAlert!)} disabled={overrideReason.length < 10 || savingOverride}
                                className="cd-ui text-[11px] px-2.5 py-1 bg-caution text-white rounded hover:brightness-110 disabled:opacity-50">
                                {savingOverride ? 'Saving…' : 'Confirm override'}
                              </button>
                              <button onClick={() => { setOverrideKey(null); setOverrideReason('') }}
                                className="cd-ui text-[11px] px-2.5 py-1 border border-line2 rounded text-ink2 hover:bg-surface2">Cancel</button>
                              <span className="cd-data text-[10px] text-ink3 ml-auto">{overrideReason.length}/10</span>
                            </div>
                          </div>
                        ) : (
                          <div className="flex gap-1.5 pt-1 border-t border-line">
                            <button onClick={() => { setOverrideKey(it.key); setOverrideReason('') }}
                              className="cd-ui text-[11px] px-2 py-0.5 rounded border border-line2 text-ink2 hover:bg-surface2 mt-1.5">Override</button>
                            <button onClick={() => setDismissed(p => new Set([...p, it.durAlert!.id]))}
                              className="cd-ui text-[11px] px-2 py-0.5 rounded border border-line2 text-ink2 hover:bg-surface2 mt-1.5">Dismiss</button>
                          </div>
                        )
                      )}
                    </div>
                  )}
                </div>
              )
            })}
          </div>
        ))}

        {routine.length > 0 && (
          <button onClick={() => setShowRoutine(s => !s)}
            className="cd-ui w-full text-[12px] text-ink2 py-1.5 rounded-lg border border-dashed border-line2 hover:bg-surface2">
            {showRoutine ? '▴ Hide routine notes' : `▾ Show ${routine.length} routine note${routine.length > 1 ? 's' : ''}`}
          </button>
        )}
      </div>

      {contraindicated.length > 0 && (
        <div className="px-3 pb-3">
          <button onClick={() => setLetterOpen(true)}
            className="cd-ui w-full px-3 py-2 bg-blocker text-white text-sm rounded-lg hover:brightness-110">
            Generate physician letter
          </button>
        </div>
      )}
      {letterOpen && patientId && (
        <PhysicianLetterModal patientId={patientId}
          findings={contraindicated.map(f => ({ rule_id: f.rule_id, participants: f.participants, mechanism: f.mechanism, severity: f.severity }))}
          onClose={() => setLetterOpen(false)} />
      )}

      {/* ── Patient history / trajectory — calm drawer (no prescriber here) ── */}
      <div className="px-3 pb-3">
        <TrajectorySignalStrip patientId={patientId} drugName={drugName} />
      </div>

      <p className="px-4 pb-3 cd-ui text-[10px] text-ink3 italic border-t border-line pt-2">
        Advisory clinical decision support — findings are review prompts, not diagnoses. A licensed
        pharmacist verifies appropriateness before any action.
      </p>
    </div>
  )
}

// ── Small parts ───────────────────────────────────────────────────────────────
function Numeral({ n, label, sev }: { n: number; label: string; sev: Severity }) {
  const tok = SEV[sev]; const muted = n === 0
  return (
    <div className="flex flex-col leading-none">
      <span className={`cd-narr text-[30px] font-semibold tabular-nums ${muted ? 'text-ink3/40' : tok.text}`}>{String(n).padStart(2, '0')}</span>
      <span className={`cd-ui text-[10px] uppercase tracking-wider mt-0.5 ${muted ? 'text-ink3/60' : 'text-ink2'}`}>{label}</span>
    </div>
  )
}
function Divider() { return <span className="h-7 w-px bg-line self-end mb-3.5" /> }

function Seg({ active, onClick, label, n }: { active: boolean; onClick: () => void; label: string; n: number }) {
  return (
    <button onClick={onClick}
      className={`cd-ui text-[11px] font-medium px-2.5 py-1 rounded-lg border transition-colors ${
        active ? 'bg-intel-soft text-intel border-intel/30' : 'text-ink3 border-transparent hover:bg-surface2'}`}>
      {label} <span className="cd-data opacity-70">{n}</span>
    </button>
  )
}
function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div>
      <div className="text-[9px] uppercase tracking-wide text-ink3 mb-0.5">{label}</div>
      <p className="cd-narr text-[12.5px] text-ink leading-[1.6]">{children}</p>
    </div>
  )
}
function Metric({ label, value }: { label: string; value: string }) {
  return (
    <div className="cd-inset px-2 py-1.5">
      <div className="text-[9px] uppercase tracking-wide text-ink3">{label}</div>
      <div className="cd-ui text-[12px] font-medium text-ink mt-0.5 leading-tight">{value}</div>
    </div>
  )
}
function ChipRow({ label, items, tone }: { label: string; items: string[]; tone: Severity }) {
  const tok = SEV[tone]
  return (
    <div>
      <div className="text-[9px] uppercase tracking-wide text-ink3 mb-1">{label}</div>
      <div className="flex flex-wrap gap-1">
        {items.map((s, i) => (
          <span key={i} className={`cd-data text-[10px] ${tok.bg} ${tok.text} border ${tok.border} rounded px-1.5 py-0.5`}>{s}</span>
        ))}
      </div>
    </div>
  )
}
