import { useMemo, useState } from 'react'
import { useMutation, useQuery } from '@tanstack/react-query'
import {
  AlertTriangle, CheckCircle2, ClipboardCheck, Dna, FlaskConical,
  Loader2, Plus, Search, ShieldAlert, Stethoscope, X,
} from 'lucide-react'
import { clinicalApi, patientApi, rxApi, type PGxGenotypeInput } from '../lib/api'

interface Patient {
  id: string
  first_name: string
  last_name: string
  date_of_birth?: string
  gender?: string
}

interface PGxInterpretation {
  gene: string
  diplotype: string | null
  phenotype: string
  drug: string
  clinical_implication: string
  suggested_pharmacist_action: string
  alternatives_or_caution: string
  evidence_source: string
  confidence: 'high' | 'moderate' | 'low'
  actionable: boolean
  pharmacist_verification_notice: string
}

interface PGxResponse {
  patient_id: string
  evaluated_at: string
  model_version: string
  genotypes: PGxGenotypeInput[]
  interpretations: PGxInterpretation[]
  missing_information: string[]
  pharmacist_verification_notice: string
  note?: string
}

const confidenceClass: Record<PGxInterpretation['confidence'], string> = {
  high: 'bg-emerald-500/15 text-emerald-300 ring-emerald-500/30',
  moderate: 'bg-amber-500/15 text-amber-300 ring-amber-500/30',
  low: 'bg-slate-500/15 text-slate-300 ring-slate-500/30',
}

function calcAge(dob?: string): number | null {
  if (!dob) return null
  return Math.floor((Date.now() - new Date(dob).getTime()) / (365.25 * 24 * 3600 * 1000))
}

function GenotypeChip({ genotype }: { genotype: PGxGenotypeInput }) {
  const detail = genotype.phenotype || genotype.diplotype || 'phenotype pending'
  return (
    <span className="inline-flex max-w-full items-center gap-1.5 rounded bg-[#0f1117] px-2 py-1 text-[11px] text-slate-300 ring-1 ring-[#253047]">
      <Dna size={12} className="flex-shrink-0 text-blue-300" />
      <span className="font-semibold text-slate-100">{genotype.gene}</span>
      <span className="truncate text-slate-400">{detail}</span>
    </span>
  )
}

function InterpretationCard({ interpretation }: { interpretation: PGxInterpretation }) {
  return (
    <div className={`rounded-lg border p-4 ${
      interpretation.actionable
        ? 'border-amber-500/30 bg-amber-500/[0.07]'
        : 'border-[#1e293b] bg-[#171c29]'
    }`}>
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-2">
            <span className={interpretation.actionable
              ? 'rounded-full bg-amber-500/15 px-2 py-0.5 text-[11px] font-bold text-amber-300 ring-1 ring-amber-500/30'
              : 'rounded-full bg-blue-500/15 px-2 py-0.5 text-[11px] font-bold text-blue-300 ring-1 ring-blue-500/30'
            }>
              {interpretation.actionable ? 'Actionable' : 'Informational'}
            </span>
            <span className={`rounded-full px-2 py-0.5 text-[11px] font-bold capitalize ring-1 ${confidenceClass[interpretation.confidence]}`}>
              {interpretation.confidence}
            </span>
          </div>
          <h2 className="mt-3 text-base font-semibold text-slate-100">
            {interpretation.gene} + {interpretation.drug}
          </h2>
          <div className="mt-2 flex flex-wrap gap-2">
            <span className="rounded bg-[#0f1117] px-2 py-1 text-[11px] text-slate-300">{interpretation.phenotype}</span>
            {interpretation.diplotype && (
              <span className="rounded bg-[#0f1117] px-2 py-1 text-[11px] text-slate-400">{interpretation.diplotype}</span>
            )}
          </div>
        </div>
        {interpretation.actionable ? <AlertTriangle size={18} className="text-amber-300" /> : <CheckCircle2 size={18} className="text-blue-300" />}
      </div>

      <div className="mt-4 grid grid-cols-1 gap-3 xl:grid-cols-2">
        <div className="rounded-lg border border-[#253047] bg-[#0f1117] p-3">
          <p className="text-[11px] font-semibold uppercase tracking-widest text-slate-500">Clinical Implication</p>
          <p className="mt-2 text-xs leading-relaxed text-slate-300">{interpretation.clinical_implication}</p>
        </div>
        <div className="rounded-lg border border-[#253047] bg-[#0f1117] p-3">
          <p className="text-[11px] font-semibold uppercase tracking-widest text-slate-500">Pharmacist Action</p>
          <p className="mt-2 text-xs leading-relaxed text-slate-300">{interpretation.suggested_pharmacist_action}</p>
        </div>
      </div>

      <div className="mt-3 rounded-lg border border-[#253047] bg-[#0f1117] p-3">
        <p className="text-[11px] font-semibold uppercase tracking-widest text-slate-500">Alternatives / Caution</p>
        <p className="mt-2 text-xs leading-relaxed text-slate-300">{interpretation.alternatives_or_caution}</p>
      </div>

      <div className="mt-3 rounded-lg border border-[#253047] bg-[#0f1117] p-3">
        <p className="text-[11px] font-semibold uppercase tracking-widest text-slate-500">Evidence Source</p>
        <p className="mt-2 text-xs leading-relaxed text-slate-300">{interpretation.evidence_source}</p>
      </div>
    </div>
  )
}

export default function Pharmacogenomics() {
  const [query, setQuery] = useState('')
  const [submitted, setSubmitted] = useState('')
  const [selected, setSelected] = useState<Patient | null>(null)
  const [drugText, setDrugText] = useState('')
  const [gene, setGene] = useState('')
  const [diplotype, setDiplotype] = useState('')
  const [phenotype, setPhenotype] = useState('')
  const [genotypeOverrides, setGenotypeOverrides] = useState<PGxGenotypeInput[]>([])

  const patientSearch = useQuery({
    queryKey: ['pgx-patient-search', submitted],
    queryFn: () => patientApi.search(submitted).then(r => r.data as Patient[]),
    enabled: submitted.length >= 2,
  })

  const rxHistory = useQuery({
    queryKey: ['pgx-rx-history', selected?.id],
    queryFn: () => rxApi.byPatient(selected!.id, 12).then(r => r.data as Array<{ id: string; drug_name: string; status: string }>),
    enabled: !!selected,
  })

  const interpret = useMutation({
    mutationFn: () => clinicalApi
      .interpretPGx(
        selected!.id,
        drugText.split(',').map(item => item.trim()).filter(Boolean),
        genotypeOverrides.length ? genotypeOverrides : undefined,
      )
      .then(r => r.data as PGxResponse),
  })

  const age = calcAge(selected?.date_of_birth)
  const displayedGenotypes = useMemo(
    () => interpret.data?.genotypes?.length ? interpret.data.genotypes : genotypeOverrides,
    [interpret.data?.genotypes, genotypeOverrides],
  )

  const addGenotype = () => {
    if (!gene.trim()) return
    setGenotypeOverrides(items => [
      ...items,
      {
        gene: gene.trim(),
        ...(diplotype.trim() ? { diplotype: diplotype.trim() } : {}),
        ...(phenotype.trim() ? { phenotype: phenotype.trim() } : {}),
        source: 'request',
      },
    ])
    setGene('')
    setDiplotype('')
    setPhenotype('')
    interpret.reset()
  }

  return (
    <div className="h-full overflow-y-auto bg-[#0f1117] p-5">
      <div className="mx-auto max-w-7xl space-y-5">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div>
            <h1 className="text-xl font-semibold text-slate-100">Pharmacogenomics</h1>
            <p className="mt-1 text-xs text-slate-500">Deterministic CPIC-based interpretation</p>
          </div>
          {interpret.data && (
            <div className="rounded-lg border border-[#1e293b] bg-[#171c29] px-3 py-2 text-right">
              <p className="text-[10px] uppercase tracking-widest text-slate-500">Model</p>
              <p className="text-xs font-mono text-slate-300">{interpret.data.model_version}</p>
            </div>
          )}
        </div>

        <div className="rounded-lg border border-[#1e293b] bg-[#1a1f2e] p-4">
          <div className="flex gap-2">
            <div className="relative flex-1">
              <Search size={15} className="absolute left-3 top-1/2 -translate-y-1/2 text-slate-600" />
              <input
                value={query}
                onChange={event => setQuery(event.target.value)}
                onKeyDown={event => {
                  if (event.key === 'Enter' && query.trim().length >= 2) setSubmitted(query.trim())
                }}
                placeholder="Patient name, DOB, or phone"
                className="w-full rounded-lg border border-[#334155] bg-[#0f1117] py-2.5 pl-9 pr-3 text-sm text-slate-100 placeholder:text-slate-600 focus:border-blue-500 focus:outline-none"
              />
            </div>
            <button
              onClick={() => setSubmitted(query.trim())}
              disabled={query.trim().length < 2 || patientSearch.isFetching}
              className="inline-flex items-center gap-2 rounded-lg bg-blue-600 px-4 py-2.5 text-sm font-medium text-white hover:bg-blue-500 disabled:opacity-40"
            >
              {patientSearch.isFetching ? <Loader2 size={15} className="animate-spin" /> : <Search size={15} />}
              Search
            </button>
          </div>
          {(patientSearch.data?.length ?? 0) > 0 && (
            <div className="mt-3 overflow-hidden rounded-lg border border-[#253047]">
              {patientSearch.data!.map(patient => (
                <button
                  key={patient.id}
                  onClick={() => {
                    setSelected(patient)
                    interpret.reset()
                    setGenotypeOverrides([])
                    setDrugText('')
                    setQuery(`${patient.last_name}, ${patient.first_name}`)
                  }}
                  className="flex w-full items-center justify-between border-b border-[#253047] px-3 py-2.5 text-left last:border-b-0 hover:bg-white/[0.03]"
                >
                  <span className="text-sm font-medium text-slate-200">{patient.last_name}, {patient.first_name}</span>
                  <span className="text-xs text-slate-500">{patient.date_of_birth} · {patient.gender}</span>
                </button>
              ))}
            </div>
          )}
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
              </div>

              <div className="rounded-lg border border-[#1e293b] bg-[#1a1f2e] p-4">
                <div className="flex items-center gap-2">
                  <FlaskConical size={17} className="text-slate-400" />
                  <h2 className="text-sm font-semibold text-slate-100">Medication Context</h2>
                </div>
                <div className="mt-3 space-y-2">
                  {(rxHistory.data ?? []).slice(0, 8).map(rx => (
                    <div key={rx.id} className="rounded bg-[#0f1117] px-3 py-2">
                      <p className="truncate text-xs font-medium text-slate-300">{rx.drug_name}</p>
                      <p className="mt-0.5 text-[11px] capitalize text-slate-600">{rx.status?.replace(/_/g, ' ')}</p>
                    </div>
                  ))}
                  {!rxHistory.isLoading && !rxHistory.data?.length && <p className="text-xs text-slate-500">No prescription rows found</p>}
                </div>
                <label className="mt-4 block">
                  <span className="text-[11px] font-semibold uppercase tracking-widest text-slate-500">Additional Drugs</span>
                  <input
                    value={drugText}
                    onChange={event => {
                      setDrugText(event.target.value)
                      interpret.reset()
                    }}
                    placeholder="clopidogrel, warfarin"
                    className="mt-2 w-full rounded-lg border border-[#334155] bg-[#0f1117] px-3 py-2.5 text-sm text-slate-100 placeholder:text-slate-600 focus:border-blue-500 focus:outline-none"
                  />
                </label>
              </div>

              <div className="rounded-lg border border-[#1e293b] bg-[#1a1f2e] p-4">
                <div className="flex items-center gap-2">
                  <Dna size={17} className="text-blue-300" />
                  <h2 className="text-sm font-semibold text-slate-100">Genotypes</h2>
                </div>
                <div className="mt-3 flex flex-wrap gap-2">
                  {displayedGenotypes.map((item, index) => (
                    <span key={`${item.gene}-${item.diplotype}-${item.phenotype}-${index}`} className="inline-flex items-center gap-1">
                      <GenotypeChip genotype={item} />
                      {index < genotypeOverrides.length && !interpret.data && (
                        <button
                          onClick={() => setGenotypeOverrides(items => items.filter((_, i) => i !== index))}
                          className="rounded bg-[#0f1117] p-1 text-slate-500 hover:text-red-300"
                          aria-label={`Remove ${item.gene}`}
                        >
                          <X size={12} />
                        </button>
                      )}
                    </span>
                  ))}
                  {!displayedGenotypes.length && <span className="text-xs text-slate-500">No genotype rows shown</span>}
                </div>

                <div className="mt-4 grid grid-cols-1 gap-2">
                  <input
                    value={gene}
                    onChange={event => setGene(event.target.value)}
                    placeholder="Gene, e.g. CYP2C19"
                    className="rounded-lg border border-[#334155] bg-[#0f1117] px-3 py-2 text-sm text-slate-100 placeholder:text-slate-600 focus:border-blue-500 focus:outline-none"
                  />
                  <input
                    value={diplotype}
                    onChange={event => setDiplotype(event.target.value)}
                    placeholder="Diplotype, e.g. *2/*2"
                    className="rounded-lg border border-[#334155] bg-[#0f1117] px-3 py-2 text-sm text-slate-100 placeholder:text-slate-600 focus:border-blue-500 focus:outline-none"
                  />
                  <input
                    value={phenotype}
                    onChange={event => setPhenotype(event.target.value)}
                    placeholder="Phenotype, e.g. poor metabolizer"
                    className="rounded-lg border border-[#334155] bg-[#0f1117] px-3 py-2 text-sm text-slate-100 placeholder:text-slate-600 focus:border-blue-500 focus:outline-none"
                  />
                  <button
                    onClick={addGenotype}
                    disabled={!gene.trim()}
                    className="inline-flex items-center justify-center gap-2 rounded-lg border border-[#334155] bg-[#0f1117] px-3 py-2 text-sm font-medium text-slate-200 hover:border-blue-500 hover:text-blue-200 disabled:opacity-40"
                  >
                    <Plus size={15} />
                    Add Genotype
                  </button>
                </div>

                <button
                  onClick={() => interpret.mutate()}
                  disabled={interpret.isPending}
                  className="mt-4 inline-flex w-full items-center justify-center gap-2 rounded-lg bg-blue-600 px-4 py-2.5 text-sm font-semibold text-white hover:bg-blue-500 disabled:opacity-40"
                >
                  {interpret.isPending ? <Loader2 size={15} className="animate-spin" /> : <ClipboardCheck size={15} />}
                  Interpret
                </button>
              </div>
            </aside>

            <main className="space-y-4">
              {interpret.data && (
                <div className="flex gap-2 rounded-lg border border-blue-500/20 bg-blue-500/10 p-3 text-xs leading-relaxed text-blue-200">
                  <ShieldAlert size={15} className="mt-0.5 flex-shrink-0" />
                  <span>{interpret.data.pharmacist_verification_notice}</span>
                </div>
              )}

              {interpret.data?.note && (
                <div className="rounded-lg border border-[#1e293b] bg-[#1a1f2e] p-4 text-sm text-slate-300">
                  {interpret.data.note}
                </div>
              )}

              {(interpret.data?.missing_information.length ?? 0) > 0 && (
                <div className="rounded-lg border border-amber-500/25 bg-amber-500/10 p-4">
                  <p className="text-[11px] font-semibold uppercase tracking-widest text-amber-300">Missing Information</p>
                  <ul className="mt-2 space-y-1.5">
                    {interpret.data!.missing_information.map(item => (
                      <li key={item} className="text-xs leading-relaxed text-amber-100">{item}</li>
                    ))}
                  </ul>
                </div>
              )}

              <div className="rounded-lg border border-[#1e293b] bg-[#1a1f2e] p-4">
                <div className="flex flex-wrap items-center justify-between gap-3">
                  <div>
                    <h2 className="text-sm font-semibold text-slate-100">Interpretations</h2>
                    <p className="mt-1 text-xs text-slate-500">
                      {interpret.data ? `${interpret.data.interpretations.length} result${interpret.data.interpretations.length === 1 ? '' : 's'} · ${new Date(interpret.data.evaluated_at).toLocaleString()}` : 'No interpretation yet'}
                    </p>
                  </div>
                  {interpret.data && (
                    <span className="rounded-full bg-slate-500/15 px-3 py-1 text-xs font-semibold text-slate-300 ring-1 ring-slate-500/30">
                      {interpret.data.model_version}
                    </span>
                  )}
                </div>
                <div className="mt-4 space-y-3">
                  {interpret.data?.interpretations.map(item => (
                    <InterpretationCard key={`${item.gene}-${item.drug}-${item.phenotype}`} interpretation={item} />
                  ))}
                  {interpret.data && interpret.data.interpretations.length === 0 && (
                    <div className="rounded-lg border border-[#253047] bg-[#0f1117] p-5 text-center text-sm text-slate-500">
                      No hardcoded PGx rule produced an interpretation for the evaluated inputs.
                    </div>
                  )}
                  {!interpret.data && (
                    <div className="rounded-lg border border-dashed border-[#253047] bg-[#0f1117] p-8 text-center">
                      <Dna size={24} className="mx-auto text-slate-600" />
                      <p className="mt-3 text-sm text-slate-500">Select a patient to interpret PGx results.</p>
                    </div>
                  )}
                </div>
              </div>
            </main>
          </div>
        ) : (
          <div className="rounded-lg border border-dashed border-[#253047] bg-[#1a1f2e] p-10 text-center">
            <Search size={28} className="mx-auto text-slate-600" />
            <p className="mt-3 text-sm text-slate-500">Search and select a patient.</p>
          </div>
        )}
      </div>
    </div>
  )
}
