/**
 * Bilingual UI — Persian ⇄ English for the whole workstation.
 * ===========================================================
 * Translations live at the call site as pairs, `t('English', 'فارسی')`, rather
 * than behind keys in a separate catalogue. In a two-language app whose strings
 * are already written inline, that trade is worth making: a key can go missing
 * or drift out of sync with its text, a pair cannot — the two readings of a
 * label are always edited together and always visible next to the markup they
 * belong to.
 *
 * Switching language also switches *direction*, *digits* and *number grouping*,
 * because a Persian pharmacy screen showing "1,250" in an LTR column is not
 * translated, it is merely half-translated.
 *
 * Dates deliberately do NOT change calendar. Iranian staff reason in Jalali;
 * flipping to English must not silently restate ۱۴۰۴/۰۵/۲۰ as a Gregorian date
 * the reader then has to convert back. English mode keeps the Jalali date and
 * renders it in Latin script — same day, readable either way.
 */
import { createContext, useCallback, useContext, useEffect, useMemo, useState } from 'react'
import type { ReactNode } from 'react'
import { formatJalali } from './jalali'

export type Lang = 'fa' | 'en'

const STORAGE_KEY = 'pharmpilot_lang'

/** Persian is the default: this is an Iranian pharmacy, and the panels the
 *  owner uses daily (coverage, catalog, inventory) are authored in Persian. */
function initialLang(): Lang {
  const stored = localStorage.getItem(STORAGE_KEY)
  return stored === 'en' || stored === 'fa' ? stored : 'fa'
}

export interface LangContextValue {
  lang: Lang
  setLang: (l: Lang) => void
  toggle: () => void
  dir: 'rtl' | 'ltr'
  isRTL: boolean
  /** Pick a reading. English first — most of this codebase is authored in it. */
  t: (en: string, fa: string) => string
  /** Same, for a stored `[en, fa]` pair. Spreading one into `t` does not work:
   *  a lookup table yields a *union* of tuple types, which TS refuses to spread. */
  tp: (pair: readonly [string, string]) => string
  /** Locale-aware number: Persian digits + Persian grouping under fa. */
  n: (value: number | null | undefined, opts?: Intl.NumberFormatOptions) => string
  /** Rial amount with its unit word. */
  money: (value: number | null | undefined) => string
  /** Jalali date — Persian script under fa, Latin script under en. */
  date: (input: Date | string | null | undefined, opts?: { short?: boolean }) => string
  /** Jalali date + clock time. */
  dateTime: (input: Date | string | null | undefined) => string
  /** Clock time only (top bars, timestamps). */
  time: (input: Date | string | null | undefined) => string
}

const LangContext = createContext<LangContextValue | null>(null)

export function LanguageProvider({ children }: { children: ReactNode }) {
  const [lang, setLangState] = useState<Lang>(initialLang)

  // The document element carries dir/lang so Tailwind logical properties,
  // text selection, caret movement and screen readers all follow the switch —
  // not just the strings we happen to have wrapped.
  useEffect(() => {
    const el = document.documentElement
    el.lang = lang
    el.dir = lang === 'fa' ? 'rtl' : 'ltr'
    el.dataset.lang = lang
    localStorage.setItem(STORAGE_KEY, lang)
  }, [lang])

  const setLang = useCallback((l: Lang) => setLangState(l), [])
  const toggle = useCallback(() => setLangState(l => (l === 'fa' ? 'en' : 'fa')), [])

  // Alt+Shift+L flips the language from anywhere, including panels that render
  // no toggle of their own.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.altKey && e.shiftKey && e.key.toLowerCase() === 'l') { toggle(); e.preventDefault() }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [toggle])

  const value = useMemo<LangContextValue>(() => {
    const locale = lang === 'fa' ? 'fa-IR' : 'en-US'
    const nf = new Intl.NumberFormat(locale)
    const n = (v: number | null | undefined, opts?: Intl.NumberFormatOptions) =>
      v == null || Number.isNaN(v) ? '—'
        : (opts ? new Intl.NumberFormat(locale, opts) : nf).format(v)
    return {
      lang,
      setLang,
      toggle,
      dir: lang === 'fa' ? 'rtl' : 'ltr',
      isRTL: lang === 'fa',
      t: (en, fa) => (lang === 'fa' ? fa : en),
      tp: pair => (lang === 'fa' ? pair[1] : pair[0]),
      n,
      money: v => (v == null ? '—' : `${n(v)} ${lang === 'fa' ? 'ریال' : 'IRR'}`),
      date: (input, opts) =>
        input ? formatJalali(input, { lang, persianDigits: lang === 'fa', short: opts?.short }) : '—',
      dateTime: input => {
        if (!input) return '—'
        const d = typeof input === 'string' ? new Date(input) : input
        if (Number.isNaN(d.getTime())) return '—'
        const day = formatJalali(d, { lang, persianDigits: lang === 'fa', short: true })
        return `${day} ${d.toLocaleTimeString(locale, { hour: '2-digit', minute: '2-digit' })}`
      },
      time: input => {
        if (!input) return '—'
        const d = typeof input === 'string' ? new Date(input) : input
        return Number.isNaN(d.getTime()) ? '—'
          : d.toLocaleTimeString(locale, { hour: '2-digit', minute: '2-digit' })
      },
    }
  }, [lang, setLang, toggle])

  return <LangContext.Provider value={value}>{children}</LangContext.Provider>
}

/**
 * Panels may be rendered outside the provider (tests, Storybook-style harnesses,
 * an error boundary that swallowed it). Falling back to a working Persian
 * context beats throwing: a missing provider must not blank a clinical screen.
 */
const FALLBACK: LangContextValue = {
  lang: 'fa', setLang: () => {}, toggle: () => {}, dir: 'rtl', isRTL: true,
  t: (_en, fa) => fa,
  tp: pair => pair[1],
  n: v => (v == null ? '—' : new Intl.NumberFormat('fa-IR').format(v)),
  money: v => (v == null ? '—' : `${new Intl.NumberFormat('fa-IR').format(v)} ریال`),
  date: input => (input ? formatJalali(input, { lang: 'fa', persianDigits: true }) : '—'),
  dateTime: input => (input ? formatJalali(input, { lang: 'fa', persianDigits: true }) : '—'),
  time: input => (input ? new Date(input).toLocaleTimeString('fa-IR', { hour: '2-digit', minute: '2-digit' }) : '—'),
}

export function useLang(): LangContextValue {
  return useContext(LangContext) ?? FALLBACK
}

/** Just the picker, for components that need nothing else. */
export function useT(): (en: string, fa: string) => string {
  return useLang().t
}

/**
 * The switch itself. `variant` matches the two design systems in the app:
 * 'dark' for the dashboard shell, 'light' for the Clinical Daylight workstation.
 */
export function LanguageToggle({ variant = 'dark', className = '' }:
  { variant?: 'dark' | 'light'; className?: string }) {
  const { lang, setLang, t } = useLang()
  const dark = variant === 'dark'
  return (
    <div
      role="group"
      aria-label={t('Interface language', 'زبان رابط کاربری')}
      title={t('Interface language (Alt+Shift+L)', 'زبان رابط کاربری (Alt+Shift+L)')}
      className={`inline-flex items-center rounded-lg p-0.5 gap-0.5 ${
        dark ? 'bg-white/[0.04] border border-white/[0.07]' : 'bg-surface2 border border-line'
      } ${className}`}
    >
      {(['fa', 'en'] as const).map(l => {
        const active = lang === l
        return (
          <button
            key={l}
            onClick={() => setLang(l)}
            aria-pressed={active}
            lang={l}
            className={`px-2 py-0.5 rounded-md text-[11px] font-semibold leading-none transition-colors ${
              active
                ? dark ? 'bg-blue-600/25 text-blue-200 ring-1 ring-inset ring-blue-500/40'
                       : 'bg-intel text-white'
                : dark ? 'text-slate-500 hover:text-slate-300'
                       : 'text-ink3 hover:text-ink2'
            }`}
          >
            {l === 'fa' ? 'فا' : 'EN'}
          </button>
        )
      })}
    </div>
  )
}
