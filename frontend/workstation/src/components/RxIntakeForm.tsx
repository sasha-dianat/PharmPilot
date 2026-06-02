/**
 * Rx Intake Form — new prescription entry for paper/fax/telephone Rxs.
 * E-prescriptions arrive automatically via Surescripts and bypass this form.
 * Keyboard-optimized: Tab moves field to field, Enter submits.
 */
import { useState } from 'react'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { rxApi } from '../lib/api'
import PatientSearch from './PatientSearch'
import DrugSearch from './DrugSearch'

interface FormData {
  patient_id: string
  prescriber_id: string
  ndc: string
  drug_name: string
  drug_strength: string
  sig_text: string
  quantity_prescribed: string
  days_supply: string
  refills_authorized: string
  written_date: string
  source: string
  daw_code: string
  dea_schedule: string
}

const INITIAL: FormData = {
  patient_id: '', prescriber_id: '', ndc: '', drug_name: '', drug_strength: '',
  sig_text: '', quantity_prescribed: '', days_supply: '30', refills_authorized: '0',
  written_date: new Date().toISOString().split('T')[0],
  source: 'paper', daw_code: '0', dea_schedule: '',
}

const DAW_CODES = [
  { value: '0', label: '0 — No product selection indicated' },
  { value: '1', label: '1 — Substitution not allowed by prescriber' },
  { value: '2', label: '2 — Substitution allowed — patient requested brand' },
  { value: '5', label: '5 — Brand dispensed as generic' },
]

export default function RxIntakeForm({ onSuccess }: { onSuccess?: () => void }) {
  const [form, setForm] = useState<FormData>(INITIAL)
  const [errors, setErrors] = useState<Partial<FormData>>({})
  const queryClient = useQueryClient()

  const intake = useMutation({
    mutationFn: (data: FormData) => rxApi.intake({
      patient_id: data.patient_id,
      prescriber_id: data.prescriber_id || '00000000-0000-0000-0000-000000000000',
      ndc: data.ndc,
      drug_name: data.drug_name,
      drug_strength: data.drug_strength || undefined,
      sig_text: data.sig_text,
      quantity_prescribed: parseFloat(data.quantity_prescribed),
      days_supply: parseInt(data.days_supply),
      refills_authorized: parseInt(data.refills_authorized),
      written_date: data.written_date,
      source: data.source,
      dea_schedule: data.dea_schedule || undefined,
    }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['prescriptions'] })
      setForm(INITIAL)
      onSuccess?.()
    },
  })

  const set = (k: keyof FormData) => (e: React.ChangeEvent<HTMLInputElement | HTMLSelectElement | HTMLTextAreaElement>) =>
    setForm(f => ({ ...f, [k]: e.target.value }))

  const validate = (): boolean => {
    const e: Partial<FormData> = {}
    if (!form.patient_id)          e.patient_id          = 'Required'
    if (!form.ndc || !form.drug_name) e.ndc               = 'Required'
    if (!form.sig_text)             e.sig_text            = 'Required'
    if (!form.quantity_prescribed)  e.quantity_prescribed = 'Required'
    if (!form.days_supply)          e.days_supply         = 'Required'
    if (!form.written_date)         e.written_date        = 'Required'
    setErrors(e)
    return Object.keys(e).length === 0
  }

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault()
    if (validate()) intake.mutate(form)
  }

  const fieldCls = (k: keyof FormData) =>
    `w-full border rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500
     ${errors[k] ? 'border-red-400 bg-red-50' : 'border-gray-300'}`

  return (
    <form onSubmit={handleSubmit} className="space-y-4 max-w-2xl">
      <h3 className="font-semibold text-gray-800 text-base">New Prescription Intake</h3>

      {/* Patient */}
      <div>
        <label className="block text-xs font-medium text-gray-600 mb-1">Patient *</label>
        <PatientSearch
          onSelect={p => setForm(f => ({ ...f, patient_id: p.id }))}
          placeholder="Search patient by name, DOB, or phone…"
        />
        {errors.patient_id && <p className="text-xs text-red-500 mt-1">{errors.patient_id}</p>}
      </div>

      {/* Drug */}
      <div>
        <label className="block text-xs font-medium text-gray-600 mb-1">Drug *</label>
        <DrugSearch
          onSelect={d => setForm(f => ({
            ...f,
            ndc: d.ndc11,
            drug_name: d.generic_name,
            drug_strength: d.strength || '',
            dea_schedule: d.dea_schedule || '',
          }))}
        />
        {form.ndc && (
          <p className="text-xs text-green-700 mt-1">
            ✓ {form.drug_name} {form.drug_strength} — NDC: {form.ndc}
            {form.dea_schedule && <span className="ml-2 bg-orange-100 text-orange-700 px-1 rounded">{form.dea_schedule}</span>}
          </p>
        )}
        {errors.ndc && <p className="text-xs text-red-500 mt-1">{errors.ndc}</p>}
      </div>

      {/* SIG */}
      <div>
        <label className="block text-xs font-medium text-gray-600 mb-1">Directions (SIG) *</label>
        <textarea
          value={form.sig_text}
          onChange={set('sig_text')}
          rows={2}
          placeholder="e.g., Take 1 tablet by mouth twice daily with food"
          className={fieldCls('sig_text') + ' resize-none'}
        />
        {errors.sig_text && <p className="text-xs text-red-500 mt-1">{errors.sig_text}</p>}
      </div>

      {/* Qty / Days / Refills */}
      <div className="grid grid-cols-3 gap-3">
        <div>
          <label className="block text-xs font-medium text-gray-600 mb-1">Quantity *</label>
          <input type="number" min="0.001" step="0.001" value={form.quantity_prescribed}
            onChange={set('quantity_prescribed')} className={fieldCls('quantity_prescribed')} placeholder="30" />
          {errors.quantity_prescribed && <p className="text-xs text-red-500 mt-1">{errors.quantity_prescribed}</p>}
        </div>
        <div>
          <label className="block text-xs font-medium text-gray-600 mb-1">Days Supply *</label>
          <input type="number" min="1" value={form.days_supply}
            onChange={set('days_supply')} className={fieldCls('days_supply')} />
        </div>
        <div>
          <label className="block text-xs font-medium text-gray-600 mb-1">Refills</label>
          <input type="number" min="0" max="12" value={form.refills_authorized}
            onChange={set('refills_authorized')} className={fieldCls('refills_authorized')} />
        </div>
      </div>

      {/* Written date / Source / DAW */}
      <div className="grid grid-cols-3 gap-3">
        <div>
          <label className="block text-xs font-medium text-gray-600 mb-1">Written Date *</label>
          <input type="date" value={form.written_date}
            onChange={set('written_date')} className={fieldCls('written_date')} />
        </div>
        <div>
          <label className="block text-xs font-medium text-gray-600 mb-1">Source</label>
          <select value={form.source} onChange={set('source')} className={fieldCls('source')}>
            <option value="paper">Paper</option>
            <option value="fax">Fax</option>
            <option value="telephone">Telephone</option>
            <option value="transfer_in">Transfer In</option>
          </select>
        </div>
        <div>
          <label className="block text-xs font-medium text-gray-600 mb-1">DAW Code</label>
          <select value={form.daw_code} onChange={set('daw_code')} className={fieldCls('daw_code')}>
            {DAW_CODES.map(d => <option key={d.value} value={d.value}>{d.label}</option>)}
          </select>
        </div>
      </div>

      {/* Error / submit */}
      {intake.isError && (
        <div className="bg-red-50 border border-red-200 rounded-lg px-3 py-2 text-sm text-red-700">
          ⚠ Intake failed. Check all required fields and try again.
        </div>
      )}

      <div className="flex gap-3 pt-2">
        <button
          type="submit"
          disabled={intake.isPending}
          className="px-6 py-2.5 bg-blue-600 text-white rounded-lg text-sm font-semibold
                     hover:bg-blue-700 disabled:opacity-50 disabled:cursor-not-allowed"
        >
          {intake.isPending ? 'Submitting…' : 'Intake Prescription'}
        </button>
        <button
          type="button"
          onClick={() => { setForm(INITIAL); setErrors({}) }}
          className="px-4 py-2.5 bg-gray-100 text-gray-700 rounded-lg text-sm hover:bg-gray-200"
        >
          Clear
        </button>
      </div>
    </form>
  )
}
