/**
 * Patient Search — fuzzy name, exact DOB, or phone search.
 * Shows biometric enrollment status and allergy count.
 * Used in Rx intake, patient lookup, and counseling workflows.
 */
import { useState, useCallback } from 'react'
import { useQuery } from '@tanstack/react-query'
import { patientApi } from '../lib/api'

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

interface Props {
  onSelect: (patient: Patient) => void
  placeholder?: string
}

function calcAge(dob: string): number {
  return Math.floor((Date.now() - new Date(dob).getTime()) / (365.25 * 24 * 3600 * 1000))
}

export default function PatientSearch({ onSelect, placeholder = 'Search patient name, DOB (YYYY-MM-DD), or phone…' }: Props) {
  const [query, setQuery] = useState('')
  const [submitted, setSubmitted] = useState('')

  const handleSearch = () => {
    if (query.trim().length >= 2) setSubmitted(query.trim())
  }

  const { data: results = [], isFetching } = useQuery({
    queryKey: ['patient-search', submitted],
    queryFn: () => patientApi.search(submitted).then(r => r.data as Patient[]),
    enabled: submitted.length >= 2,
  })

  return (
    <div className="space-y-3">
      {/* Search input */}
      <div className="flex gap-2">
        <input
          type="text"
          value={query}
          onChange={e => setQuery(e.target.value)}
          onKeyDown={e => e.key === 'Enter' && handleSearch()}
          placeholder={placeholder}
          className="flex-1 border border-gray-300 rounded-lg px-4 py-2.5 text-sm
                     focus:outline-none focus:ring-2 focus:ring-blue-500"
        />
        <button
          onClick={handleSearch}
          disabled={query.length < 2}
          className="px-4 py-2.5 bg-blue-600 text-white rounded-lg text-sm font-medium
                     hover:bg-blue-700 disabled:opacity-40"
        >
          {isFetching ? '…' : 'Search'}
        </button>
      </div>

      {/* Results */}
      {submitted && !isFetching && results.length === 0 && (
        <p className="text-sm text-gray-500 px-1">No patients found for "{submitted}"</p>
      )}

      {results.length > 0 && (
        <div className="border border-gray-200 rounded-lg overflow-hidden">
          {results.map((patient, idx) => (
            <button
              key={patient.id}
              onClick={() => { onSelect(patient); setQuery(`${patient.last_name}, ${patient.first_name}`) }}
              className={`w-full px-4 py-3 text-left hover:bg-blue-50 flex items-center justify-between
                          ${idx < results.length - 1 ? 'border-b border-gray-100' : ''}`}
            >
              <div>
                <span className="font-medium text-gray-900">
                  {patient.last_name}, {patient.first_name}
                </span>
                <span className="text-gray-400 text-xs ml-2">
                  DOB: {patient.date_of_birth} ({calcAge(patient.date_of_birth)}y) · {patient.gender}
                </span>
                {patient.phone_primary && (
                  <span className="text-gray-400 text-xs ml-2">{patient.phone_primary}</span>
                )}
              </div>
              <div className="flex items-center gap-2 flex-shrink-0">
                {patient.biometric_enrolled && (
                  <span className="text-xs bg-green-100 text-green-700 px-1.5 py-0.5 rounded">
                    Biometric ✓
                  </span>
                )}
                {patient.status !== 'active' && (
                  <span className="text-xs bg-gray-100 text-gray-500 px-1.5 py-0.5 rounded capitalize">
                    {patient.status}
                  </span>
                )}
              </div>
            </button>
          ))}
        </div>
      )}
    </div>
  )
}
