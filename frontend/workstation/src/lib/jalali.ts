/**
 * Jalali (Shamsi / شمسی) calendar utilities for the frontend.
 * ============================================================
 * Pure TypeScript, zero dependencies. Converts between Gregorian
 * and Jalali, formats dates in Persian/English, and handles
 * Persian digit display for the Iranian default configuration.
 *
 * Mirrors the backend services/core/localization/jalali.py algorithm
 * (Borkowski / Noruzi Niya day-count method) — same reference dates.
 */

const J_DAYS_IN_MONTH = [31, 31, 31, 31, 31, 31, 30, 30, 30, 30, 30, 29]

const JALALI_MONTHS_FA = [
  '', 'فروردین', 'اردیبهشت', 'خرداد', 'تیر', 'مرداد', 'شهریور',
  'مهر', 'آبان', 'آذر', 'دی', 'بهمن', 'اسفند',
]
const JALALI_MONTHS_EN = [
  '', 'Farvardin', 'Ordibehesht', 'Khordad', 'Tir', 'Mordad', 'Shahrivar',
  'Mehr', 'Aban', 'Azar', 'Dey', 'Bahman', 'Esfand',
]

const _PERSIAN_DIGITS = '۰۱۲۳۴۵۶۷۸۹'

export function toPersianDigits(n: number | string): string {
  return String(n).replace(/[0-9]/g, d => _PERSIAN_DIGITS[+d])
}

export function toAsciiDigits(s: string): string {
  return s.replace(/[۰-۹]/g, d => String(_PERSIAN_DIGITS.indexOf(d)))
}

/** Convert Gregorian (year, month 1-based, day) to Jalali. */
export function gregorianToJalali(gy: number, gm: number, gd: number): [number, number, number] {
  const jy0 = gy - 1600; const jm0 = gm - 1; const jd0 = gd - 1
  const gMonths = [31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31]
  let gDayNo = 365 * jy0 + Math.floor((jy0 + 3) / 4) - Math.floor((jy0 + 99) / 100) + Math.floor((jy0 + 399) / 400)
  for (let i = 0; i < jm0; i++) gDayNo += gMonths[i]
  if (jm0 > 1 && ((gy % 4 === 0 && gy % 100 !== 0) || gy % 400 === 0)) gDayNo++
  gDayNo += jd0

  let jDayNo = gDayNo - 79
  const jNp = Math.floor(jDayNo / 12053); jDayNo %= 12053
  let jy = 979 + 33 * jNp + 4 * Math.floor(jDayNo / 1461); jDayNo %= 1461
  if (jDayNo >= 366) { jy += Math.floor((jDayNo - 1) / 365); jDayNo = (jDayNo - 1) % 365 }
  let jm = 0, jd = 0
  for (let i = 0; i < 11; i++) {
    if (jDayNo < J_DAYS_IN_MONTH[i]) { jm = i + 1; jd = jDayNo + 1; break }
    jDayNo -= J_DAYS_IN_MONTH[i]
  }
  if (jm === 0) { jm = 12; jd = jDayNo + 1 }
  return [jy, jm, jd]
}

/** Format a Date or ISO string as Jalali, e.g. '۱۵ خرداد ۱۴۰۳' */
export function formatJalali(
  input: Date | string | null | undefined,
  opts: { lang?: 'fa' | 'en'; persianDigits?: boolean; short?: boolean } = {}
): string {
  if (!input) return '—'
  const { lang = 'fa', persianDigits = true, short = false } = opts
  try {
    const d = typeof input === 'string' ? new Date(input) : input
    if (isNaN(d.getTime())) return String(input)
    const [jy, jm, jd] = gregorianToJalali(d.getFullYear(), d.getMonth() + 1, d.getDate())
    if (short) {
      const y = persianDigits ? toPersianDigits(jy) : String(jy)
      const m = persianDigits ? toPersianDigits(String(jm).padStart(2, '0')) : String(jm).padStart(2, '0')
      const day = persianDigits ? toPersianDigits(String(jd).padStart(2, '0')) : String(jd).padStart(2, '0')
      return lang === 'fa' ? `${y}/${m}/${day}` : `${jy}/${String(jm).padStart(2,'0')}/${String(jd).padStart(2,'0')}`
    }
    const monthName = lang === 'fa' ? JALALI_MONTHS_FA[jm] : JALALI_MONTHS_EN[jm]
    const dayStr  = persianDigits ? toPersianDigits(jd) : String(jd)
    const yearStr = persianDigits ? toPersianDigits(jy) : String(jy)
    return `${dayStr} ${monthName} ${yearStr}`
  } catch {
    return String(input)
  }
}

/** Age in years from a date of birth (calendar-agnostic). */
export function ageFromDob(dob: string | Date | null | undefined): number | null {
  if (!dob) return null
  try {
    const d = typeof dob === 'string' ? new Date(dob) : dob
    if (isNaN(d.getTime())) return null
    const today = new Date()
    let age = today.getFullYear() - d.getFullYear()
    const monthDiff = today.getMonth() - d.getMonth()
    if (monthDiff < 0 || (monthDiff === 0 && today.getDate() < d.getDate())) age--
    return age
  } catch { return null }
}

/**
 * Render a date as Jalali (primary) with Gregorian in parentheses.
 * Used throughout the workstation for DOB, fill dates, etc.
 */
export function dateDisplay(
  input: Date | string | null | undefined,
  showGregorian = false,
  lang: 'fa' | 'en' = 'fa',
): string {
  if (!input) return '—'
  const jalali = formatJalali(input, { lang, short: true })
  if (!showGregorian) return jalali
  const d = typeof input === 'string' ? new Date(input) : input
  const gregorian = isNaN(d.getTime()) ? '' : d.toISOString().slice(0, 10)
  return `${jalali} (${gregorian})`
}
