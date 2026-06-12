import { useMemo, useState } from 'react'
import { useMutation } from '@tanstack/react-query'
import {
  AlertTriangle, CheckCircle2, ChevronDown, ChevronRight, ClipboardCheck,
  FlaskConical, Loader2, ShieldAlert, Stethoscope,
} from 'lucide-react'
import PatientSearch from '../components/PatientSearch'
import { clinicalApi } from '../lib/api'

interface Patient {
  id: string
  first_name: string
  last_name: string
  date_of_birth: string
  gender: string
  phone_primary?: string
  status: string
  biometric_enrolled: boolean
}

interface LabSafetyFinding {
  rule_id: string
  severity: 'critical' | 'high' | 'moderate' | 'low'
  drug: string
  lab_name: string
  lab_value: string
  lab_unit: string | null
  lab_date: string
  threshold_triggered: string
  explanation: string
  suggested_pharmacist_action: string
  evidence_source: string
  confidence: number
  pharmacist_verification_notice: string
}

interface MissingLab {
  drug: string
  lab_name: string
  reason: string
}

interface LabSafetyResponse {
  patient_id: string
  findings: LabSafetyFinding[]
  missing_labs: MissingLab[]
  drugs_evaluated: string[]
  labs_evaluated: string[]
  assessment_date: string
  pharmacist_verification_notice: string
}

const severityClass: Record<LabSafetyFinding['severity'], string> = {
  critical: 'bg-red-700 text-white ring-red-400/40',
  high: 'bg-orange-600 text-white ring-orange-300/40',
  moderate: 'bg-yellow-600 text-yellow-950 ring-yellow-300/40',
  low: 'bg-blue-600 text-white ring-blue-300/40',
}

const severityIconClass: Record<LabSafetyFinding['severity'], string> = {
  critical: 'text-red-300',
  high: 'text-orange-300',
  moderate: 'text-yellow-300',
  low: 'text-blue-300',
}

function calcAge(dob?: string): number | null {
  if (!dob) return null
  return Math.floor((Date.now() - new Date(dob).getTime()) / (365.25 * 24 * 3600 * 1000))
}

function FindingCard({ finding }: { finding: LabSafetyFinding }) {
  return (
    <div className="rounded-lg border border-[#1e293b] bg-[#171c29] p-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-2">
            <span className={`rounded-full px-2.5 py-1 text-[11px] font-bold capitalize ring-1 ${severityClass[finding.severity]}`}>
              {finding.severity}
            </span>
            <span className="rounded bg-[#0f1117] px-2 py-1 text-[11px] font-mono text-slate-300">
              {finding.rule_id}
            </span>
            <span className="rounded bg-[#0f1117] px-2 py-1 text-[11px] text-slate-400">
              {Math.round(finding.confidence * 100)}%
            </span>
          </div>
          <h2 className="mt-3 text-base font-semibold text-slate-100">
            {finding.drug} · {finding.lab_name}
          </h2>
          <p className="mt-1 text-xs text-slate-500">
            {finding.lab_value}{finding.lab_unit ? ` ${finding.lab_unit}` : ''} · {finding.lab_date}
          </p>
        </div>
        <AlertTriangle size={19} className={severityIconClass[finding.severity]} />
      </div>

      <div className="mt-4 grid grid-cols-1 gap-3 xl:grid-cols-2">
        <div className="rounded-lg border border-[#253047] bg-[#0f1117] p-3">
          <p className="text-[11px] font-semibold uppercase tracking-widest text-slate-500">Threshold</p>
          <p className="mt-2 text-xs leading-relaxed text-slate-300">{finding.threshold_triggered}</p>
        </div>
        <div className="rounded-lg border border-[#253047] bg-[#0f1117] p-3">
          <p className="text-[11px] font-semibold uppercase tracking-widest text-slate-500">Pharmacist Action</p>
          <p className="mt-2 text-xs leading-relaxed text-slate-300">{finding.suggested_pharmacist_action}</p>
        </div>
      </div>

      <div className="mt-3 rounded-lg border border-[#253047] bg-[#0f1117] p-3">
        <p className="text-[11px] font-semibold uppercase tracking-widest text-slate-500">Explanation</p>
        <p className="mt-2 text-xs leading-relaxed text-slate-300">{finding.explanation}</p>
      </div>

      <div className="mt-3 rounded-lg border border-[#253047] bg-[#0f1117] p-3">
        <p className="text-[11px] font-semibold uppercase tracking-widest text-slate-500">Evidence Source</p>
        <p className="mt-2 text-xs leading-relaxed text-slate-300">{finding.evidence_source}</p>
        <div className="mt-3 h-1.5 overflow-hidden rounded-full bg-slate-800">
          <div className="h-full rounded-full bg-blue-400" style={{ width: `${Math.round(finding.confidence * 100)}%` }} />
        </div>
      </div>
    </div>
  )
}

function SummaryBadge({ label, count, tone }: { label: string; count: number; tone: string }) {
  return (
    <div className={`rounded-lg border px-3 py-2 ${tone}`}>
      <p className="text-[10px] font-semibold uppercase tracking-widest opacity-80">{label}</p>
      <p className="mt-1 text-2xl font-semibold tabular-nums">{count}</p>
    </div>
  )
}

export default function LabSafetyPage() {
  const [selected, setSelected] = useState<Patient | null>(null)
  const [missingOpen, setMissingOpen] = useState(true)

  const assessment = useMutation({
    mutationFn: (patientId: string) => clinicalApi.labSafetyAssess(patientId).then(r => r.data as LabSafetyResponse),
  })

  const counts = useMemo(() => {
    const next = { critical: 0, high: 0, moderate: 0, low: 0 }
    for (const finding of assessment.data?.findings ?? []) next[finding.severity] += 1
    return next
  }, [assessment.data?.findings])
  const age = calcAge(selected?.date_of_birth)
  const hasCleanResult = assessment.data && assessment.data.findings.length === 0 && assessment.data.missing_labs.length === 0

  return (
    <div className="h-full overflow-y-auto bg-[#0f1117] p-5">
      <div className="mx-auto max-w-7xl space-y-5">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div>
            <h1 className="text-xl font-semibold text-slate-100">Lab Safety Monitoring</h1>
            <p className="mt-1 text-xs text-slate-500">Deterministic lab-based medication safety checks</p>
          </div>
          {assessment.data && (
            <div className="rounded-lg border border-[#1e293b] bg-[#171c29] px-3 py-2 text-right">
              <p className="text-[10px] uppercase tracking-widest text-slate-500">Assessed</p>
              <p className="text-xs font-mono text-slate-300">{new Date(assessment.data.assessment_date).toLocaleString()}</p>
            </div>
          )}
        </div>

        <div className="rounded-lg border border-[#1e293b] bg-[#1a1f2e] p-4">
          <PatientSearch
            placeholder="Search patient name, DOB, or phone"
            onSelect={(patient) => {
              setSelected(patient)
              assessment.reset()
            }}
          />
        </div>

        {selected ? (
          <div className="grid grid-cols-1 gap-5 xl:grid-cols-[360px_1fr]">
            <aside className="space-y-5">
              <div className="rounded-lg border border-[#1e293b] bg-[#1a1f2e] p-4">
                <div className="flex items-center gap-2">
                  <Stethoscope size={18} className="text-blue-300" />
                  <h2 className="text-sm font-semibold text-slate-100">{selected.first_name} {selected.last_name}</h2>
                </div>
                <dl className="mt-4 grid grid-cols-2 gap-3 text-xs">
                  <div><dt className="text-slate-500">Age</dt><dd className="mt-1 text-slate-200">{age ?? 'Unknown'}</dd></div>
                  <div><dt className="text-slate-500">Sex</dt><dd className="mt-1 text-slate-200">{selected.gender || 'Unknown'}</dd></div>
                </dl>
                <button
                  onClick={() => assessment.mutate(selected.id)}
                  disabled={assessment.isPending}
                  className="mt-4 inline-flex w-full items-center justify-center gap-2 rounded-lg bg-blue-600 px-4 py-2.5 text-sm font-semibold text-white hover:bg-blue-500 disabled:opacity-40"
                >
                  {assessment.isPending ? <Loader2 size={15} className="animate-spin" /> : <ClipboardCheck size={15} />}
                  Assess Lab Safety
                </button>
              </div>

              {assessment.data && (
                <div className="rounded-lg border border-[#1e293b] bg-[#1a1f2e] p-4">
                  <div className="flex items-center gap-2">
                    <FlaskConical size={17} className="text-slate-400" />
                    <h2 className="text-sm font-semibold text-slate-100">Evaluation Scope</h2>
                  </div>
                  <div className="mt-3 space-y-3 text-xs">
                    <div>
                      <p className="text-slate-500">Drugs evaluated</p>
                      <p className="mt-1 text-slate-300">{assessment.data.drugs_evaluated.length || 0}</p>
                    </div>
                    <div>
                      <p className="text-slate-500">Labs matched</p>
                      <div className="mt-2 flex flex-wrap gap-1.5">
                        {assessment.data.labs_evaluated.length ? assessment.data.labs_evaluated.map(lab => (
                          <span key={lab} className="rounded bg-[#0f1117] px-2 py-1 text-[11px] text-slate-300">{lab}</span>
                        )) : <span className="text-slate-500">None</span>}
                      </div>
                    </div>
                  </div>
                </div>
              )}
            </aside>

            <main className="space-y-4">
              {assessment.data && (
                <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
                  <SummaryBadge label="Critical" count={counts.critical} tone="border-red-500/30 bg-red-500/10 text-red-200" />
                  <SummaryBadge label="High" count={counts.high} tone="border-orange-500/30 bg-orange-500/10 text-orange-200" />
                  <SummaryBadge label="Moderate" count={counts.moderate} tone="border-yellow-500/30 bg-yellow-500/10 text-yellow-200" />
                  <SummaryBadge label="Low" count={counts.low} tone="border-blue-500/30 bg-blue-500/10 text-blue-200" />
                </div>
              )}

              {hasCleanResult && (
                <div className="flex gap-2 rounded-lg border border-emerald-500/25 bg-emerald-500/10 p-4 text-sm text-emerald-200">
                  <CheckCircle2 size={18} className="mt-0.5 flex-shrink-0" />
                  <span>No safety concerns identified</span>
                </div>
              )}

              {(assessment.data?.findings.length ?? 0) > 0 && (
                <div className="space-y-3">
                  {assessment.data!.findings.map(finding => (
                    <FindingCard key={`${finding.rule_id}-${finding.drug}-${finding.lab_name}`} finding={finding} />
                  ))}
                </div>
              )}

              {assessment.data && (
                <div className="rounded-lg border border-[#1e293b] bg-[#1a1f2e]">
                  <button
                    onClick={() => setMissingOpen(open => !open)}
                    className="flex w-full items-center justify-between px-4 py-3 text-left"
                  >
                    <span className="text-sm font-semibold text-slate-100">Missing Labs ({assessment.data.missing_labs.length})</span>
                    {missingOpen ? <ChevronDown size={16} className="text-slate-500" /> : <ChevronRight size={16} className="text-slate-500" />}
                  </button>
                  {missingOpen && (
                    <div className="border-t border-[#1e293b] p-4">
                      {assessment.data.missing_labs.length ? (
                        <div className="space-y-2">
                          {assessment.data.missing_labs.map((item, index) => (
                            <div key={`${item.drug}-${item.lab_name}-${index}`} className="rounded-lg border border-amber-500/20 bg-amber-500/10 px-3 py-2">
                              <p className="text-xs font-semibold text-amber-200">{item.drug} · {item.lab_name}</p>
                              <p className="mt-1 text-[11px] text-amber-100/80">{item.reason}</p>
                            </div>
                          ))}
                        </div>
                      ) : (
                        <p className="text-xs text-slate-500">No missing required labs for evaluated rules.</p>
                      )}
                    </div>
                  )}
                </div>
              )}

              {assessment.data && (
                <div className="flex gap-2 rounded-lg border border-[#1e293b] bg-[#171c29] p-3 text-xs italic leading-relaxed text-slate-400">
                  <ShieldAlert size={15} className="mt-0.5 flex-shrink-0 text-slate-500" />
                  <span>{assessment.data.pharmacist_verification_notice}</span>
                </div>
              )}

              {!assessment.data && (
                <div className="rounded-lg border border-dashed border-[#253047] bg-[#1a1f2e] p-10 text-center">
                  <FlaskConical size={28} className="mx-auto text-slate-600" />
                  <p className="mt-3 text-sm text-slate-500">Select a patient and run a deterministic lab safety assessment.</p>
                </div>
              )}
            </main>
          </div>
        ) : (
          <div className="rounded-lg border border-dashed border-[#253047] bg-[#1a1f2e] p-10 text-center">
            <Stethoscope size={28} className="mx-auto text-slate-600" />
            <p className="mt-3 text-sm text-slate-500">Search and select a patient.</p>
          </div>
        )}
      </div>
    </div>
  )
}
