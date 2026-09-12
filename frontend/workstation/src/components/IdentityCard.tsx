/**
 * IdentityCard — Reception / Biometric arrival auto-identity panel.
 * =================================================================
 * Shows the ranked candidate set returned by the identity orchestrator
 * (/api/v1/identity/identify) as tap-to-select cards. High-confidence
 * single match is auto-highlighted; the pharmacist only acts on conflict.
 *
 * Presented as a banner overlay when a biometric-arrival event fires OR
 * when a transcript/Rx-photo identity resolve is triggered.
 *
 * Zero manual entry in the happy path: the pharmacist taps one card (or
 * does nothing if auto-selected) and the workstation loads the profile.
 */
import { useState } from 'react'
import { formatJalali } from '../lib/jalali'
import { useLang } from '../lib/i18n'

export interface IdentityCandidate {
  patient_id: string
  national_id?: string | null
  name: string
  name_fa?: string | null
  dob?: string | null
  dob_jalali?: string | null
  gender?: string | null
  relationship_hint?: string | null
  is_self: boolean
  confidence: number
  active_rx_count: number
  allergies: string[]
  loyalty_points: number
  total_visits: number
}

interface IdentityResolution {
  customer_id?: string | null
  primary_candidate_id?: string | null
  candidates: IdentityCandidate[]
  is_returning_customer: boolean
  auto_loaded: boolean
  message: string
  extracted?: {
    national_code?: string
    national_code_valid?: boolean
    first_name?: string
    last_name?: string
    confidence?: number
  }
  insurance?: {
    available: boolean
    primary_org?: string
  }
}

interface Props {
  resolution: IdentityResolution
  onSelect: (patientId: string) => void
  onDismiss: () => void
}

const GENDER_LABEL: Record<string, string> = { M: '♂', F: '♀', U: '?', O: '⚧' }
const ORG_LABEL: Record<string, string> = {
  salamat: 'بیمه سلامت',          // insurer brands stay in their own name
  tamin: 'تأمین اجتماعی',
  armed_forces: 'بیمه نیروهای مسلح',
  supplementary: 'بیمه تکمیلی',
}

export default function IdentityCard({ resolution, onSelect, onDismiss }: Props) {
  const { t } = useLang()
  const [selected, setSelected] = useState<string | null>(
    resolution.auto_loaded && resolution.candidates.length === 1
      ? resolution.candidates[0].patient_id
      : null
  )

  const handleSelect = (patientId: string) => {
    setSelected(patientId)
  }

  const handleConfirm = () => {
    if (selected) onSelect(selected)
  }

  const { candidates, is_returning_customer, message, extracted, insurance } = resolution
  const autoSingle = resolution.auto_loaded && candidates.length === 1

  return (
    <div className="fixed inset-0 bg-black/50 backdrop-blur-sm z-50 flex items-start justify-center pt-16 px-4">
      <div className="bg-white rounded-2xl shadow-2xl w-full max-w-2xl overflow-hidden">

        {/* Header */}
        <div className={`px-5 py-3 flex items-center gap-3 ${
          is_returning_customer ? 'bg-blue-600' : 'bg-indigo-700'
        } text-white`}>
          <span className="text-2xl">{is_returning_customer ? '👋' : '👤'}</span>
          <div className="flex-1">
            <div className="font-bold text-sm">
              {is_returning_customer ? t('Returning customer', 'بازگشت مجدد') : t('Patient identified', 'شناسایی بیمار')}
            </div>
            <div className="text-xs text-white/75 truncate">{message}</div>
          </div>
          {insurance?.available && insurance.primary_org && (
            <span className="text-[10px] bg-white/20 rounded px-2 py-0.5">
              {ORG_LABEL[insurance.primary_org] || insurance.primary_org}
            </span>
          )}
          {extracted?.national_code && (
            <span className="font-mono text-xs bg-white/20 rounded px-2 py-0.5">
              {extracted.national_code_valid ? '✓' : '?'} {extracted.national_code.slice(0, 3)}****{extracted.national_code.slice(-2)}
            </span>
          )}
          <button onClick={onDismiss} className="text-white/60 hover:text-white text-lg leading-none">×</button>
        </div>

        {/* Candidate cards */}
        <div className="p-4 space-y-3 max-h-96 overflow-y-auto">
          {candidates.length === 0 ? (
            <div className="text-center py-8 text-gray-400">
              <div className="text-3xl mb-2">🔍</div>
              <div className="text-sm">No patient profile identified.</div>
              <div className="text-xs mt-1">Use manual search or register a new patient.</div>
            </div>
          ) : (
            candidates.map(c => (
              <button
                key={c.patient_id}
                onClick={() => handleSelect(c.patient_id)}
                className={`w-full text-left rounded-xl border-2 p-3 transition-all ${
                  selected === c.patient_id
                    ? 'border-blue-500 bg-blue-50 shadow-md'
                    : 'border-gray-200 bg-white hover:border-blue-300 hover:bg-gray-50'
                }`}
              >
                <div className="flex items-start gap-3">
                  {/* Avatar / gender icon */}
                  <div className={`w-10 h-10 rounded-full flex items-center justify-center text-lg flex-shrink-0 ${
                    c.gender === 'F' ? 'bg-pink-100' : 'bg-blue-100'
                  }`}>
                    {GENDER_LABEL[c.gender || 'U']}
                  </div>

                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-2 flex-wrap">
                      {/* Persian name (RTL) */}
                      <span className="font-bold text-gray-900 text-sm" dir="rtl">
                        {c.name_fa || c.name}
                      </span>
                      {c.is_self && (
                        <span className="text-[9px] bg-green-100 text-green-700 border border-green-300 rounded px-1.5 py-0.5 font-semibold">
                          {t('Self', 'خود بیمار')}
                        </span>
                      )}
                      {!c.is_self && c.relationship_hint && (
                        <span className="text-[9px] bg-purple-100 text-purple-700 rounded px-1.5 py-0.5">
                          {c.relationship_hint}
                        </span>
                      )}
                      {c.active_rx_count > 0 && (
                        <span className="text-[9px] bg-orange-100 text-orange-700 rounded px-1.5 py-0.5">
                          {t(`${c.active_rx_count} active Rx`, `${c.active_rx_count} نسخهٔ فعال`)}
                        </span>
                      )}
                    </div>

                    <div className="text-xs text-gray-500 mt-0.5 flex flex-wrap gap-2">
                      {(c.dob_jalali || c.dob) && (
                        <span>
                          {t('DOB', 'ت.ت')}: {c.dob_jalali || formatJalali(c.dob, { short: true, persianDigits: false })}
                        </span>
                      )}
                      {c.national_id && (
                        <span className="font-mono">
                          {t('ID', 'کد')}: {c.national_id.slice(0, 3)}****{c.national_id.slice(-2)}
                        </span>
                      )}
                    </div>

                    {c.allergies.length > 0 && (
                      <div className="mt-1 flex gap-1 flex-wrap">
                        {c.allergies.slice(0, 3).map((a, i) => (
                          <span key={i} className="text-[9px] bg-red-100 text-red-700 rounded px-1 py-0.5">⚠ {a}</span>
                        ))}
                      </div>
                    )}
                  </div>

                  <div className="flex flex-col items-end gap-1 flex-shrink-0 text-[10px] text-gray-400">
                    <div className="font-semibold text-blue-600 text-xs">
                      {Math.round(c.confidence * 100)}%
                    </div>
                    {c.total_visits > 0 && (
                      <div>⭐ {c.loyalty_points} pts</div>
                    )}
                    {c.total_visits > 1 && (
                      <div>{c.total_visits} visits</div>
                    )}
                    {selected === c.patient_id && (
                      <div className="text-blue-500 font-bold">✓ Selected</div>
                    )}
                  </div>
                </div>
              </button>
            ))
          )}
        </div>

        {/* Action bar */}
        <div className="flex items-center gap-3 px-5 py-3 bg-gray-50 border-t">
          {autoSingle && !selected && (
            <div className="text-xs text-green-700 font-medium">
              ✅ Auto-selected: {candidates[0].name}
            </div>
          )}
          <div className="flex-1" />
          {candidates.length > 0 && (
            <button
              onClick={handleConfirm}
              disabled={!selected && !autoSingle}
              className="px-5 py-2 bg-blue-600 text-white text-sm rounded-lg hover:bg-blue-700 font-medium disabled:opacity-40"
            >
              {autoSingle && !selected
                ? `Load ${candidates[0].name.split(' ')[0]}'s Profile`
                : selected
                ? 'Load Selected Profile'
                : 'Select a patient above'}
            </button>
          )}
          <button
            onClick={onDismiss}
            className="px-4 py-2 text-gray-500 text-sm rounded-lg border border-gray-200 hover:bg-gray-100"
          >
            Manual Search
          </button>
        </div>
      </div>
    </div>
  )
}
