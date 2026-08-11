/**
 * PharmPilot Login Page
 * Authenticates pharmacy staff and stores JWT in localStorage.
 * Shows role badge so the right person knows they're on the right account.
 */
import { useState, useRef, useEffect } from 'react'
import { authApi } from '../lib/api'
import { LanguageToggle, useLang } from '../lib/i18n'

interface Props {
  onLoginSuccess: (role: string, pharmacyId: string) => void
}

export default function LoginPage({ onLoginSuccess }: Props) {
  const [username, setUsername]     = useState('')
  const [password, setPassword]     = useState('')
  const [loading, setLoading]       = useState(false)
  const [error, setError]           = useState('')
  const [apiStatus, setApiStatus]   = useState<'checking' | 'ok' | 'error'>('checking')
  const usernameRef = useRef<HTMLInputElement>(null)

  // Focus username on mount
  useEffect(() => { usernameRef.current?.focus() }, [])

  // Check API health once on mount
  useEffect(() => {
    const API = import.meta.env.VITE_API_URL || 'http://localhost:8001/api/v1'
    fetch(API.replace('/api/v1', '/health'))
      .then(r => r.ok ? setApiStatus('ok') : setApiStatus('error'))
      .catch(() => setApiStatus('error'))
  }, [])

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    if (!username.trim() || !password.trim()) return
    setLoading(true)
    setError('')

    try {
      const { data } = await authApi.login(username.trim(), password)
      // Store tokens
      localStorage.setItem('access_token', data.access_token)
      localStorage.setItem('refresh_token', data.refresh_token)
      localStorage.setItem('user_role', data.role)
      localStorage.setItem('pharmacy_id', data.pharmacy_id)
      localStorage.setItem('staff_id', data.staff_id)
      onLoginSuccess(data.role, data.pharmacy_id)
    } catch (err: unknown) {
      const axiosErr = err as { response?: { status: number; data?: { detail: string } } }
      if (axiosErr.response?.status === 401) {
        setError('Invalid username or password.')
      } else if (axiosErr.response?.status === 423) {
        setError('Account locked. Contact your pharmacy administrator.')
      } else {
        setError('Cannot reach the PharmPilot server. Check that it is running.')
      }
      setPassword('')
    } finally {
      setLoading(false)
    }
  }

  const { t, dir } = useLang()

  return (
    <div dir={dir} className="min-h-screen bg-gradient-to-br from-blue-950 via-blue-900 to-indigo-900 flex items-center justify-center p-4">
      <div className="w-full max-w-sm">

        {/* The language switch must be reachable before sign-in — a pharmacist
            who cannot read the form cannot get past it. */}
        <div className="flex justify-center mb-4"><LanguageToggle variant="dark" /></div>

        {/* Logo & title */}
        <div className="text-center mb-8">
          <div className="text-5xl mb-3">💊</div>
          <h1 className="text-3xl font-bold text-white tracking-tight">PharmPilot</h1>
          <p className="text-blue-300 text-sm mt-1">{t('Pharmacy Intelligence Platform', 'سامانهٔ هوشمند داروخانه')}</p>
        </div>

        {/* Card */}
        <div className="bg-white rounded-2xl shadow-2xl p-8">
          <h2 className="text-xl font-semibold text-gray-800 mb-6 text-center">
            {t('Staff Sign In', 'ورود کارکنان')}
          </h2>

          <form onSubmit={handleSubmit} className="space-y-4">
            {/* Username */}
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">
                {t('Username', 'نام کاربری')}
              </label>
              <input
                ref={usernameRef}
                type="text"
                value={username}
                onChange={e => setUsername(e.target.value)}
                autoComplete="username"
                placeholder={t('Enter your username', 'نام کاربری خود را وارد کنید')}
                className="w-full border border-gray-300 rounded-lg px-4 py-2.5 text-sm
                           focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-transparent
                           disabled:bg-gray-50"
                disabled={loading}
              />
            </div>

            {/* Password */}
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">
                {t('Password', 'گذرواژه')}
              </label>
              <input
                type="password"
                value={password}
                onChange={e => setPassword(e.target.value)}
                autoComplete="current-password"
                placeholder={t('Enter your password', 'گذرواژهٔ خود را وارد کنید')}
                className="w-full border border-gray-300 rounded-lg px-4 py-2.5 text-sm
                           focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-transparent
                           disabled:bg-gray-50"
                disabled={loading}
              />
            </div>

            {/* Error */}
            {error && (
              <div className="flex items-start gap-2 bg-red-50 border border-red-200 rounded-lg px-3 py-2.5 text-sm text-red-700">
                <span className="mt-0.5 flex-shrink-0">⚠️</span>
                <span>{error}</span>
              </div>
            )}

            {/* Submit */}
            <button
              type="submit"
              disabled={loading || !username || !password}
              className="w-full bg-blue-600 hover:bg-blue-700 disabled:opacity-50 disabled:cursor-not-allowed
                         text-white font-semibold rounded-lg py-2.5 text-sm transition-colors"
            >
              {loading ? (
                <span className="flex items-center justify-center gap-2">
                  <svg className="animate-spin h-4 w-4" viewBox="0 0 24 24" fill="none">
                    <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4"/>
                    <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8v8z"/>
                  </svg>
                  {t('Signing in…', 'در حال ورود…')}
                </span>
              ) : t('Sign In', 'ورود')}
            </button>
          </form>

          {/* API status */}
          <div className="mt-5 pt-4 border-t border-gray-100 flex items-center justify-center gap-1.5 text-xs text-gray-400">
            <span className={`w-1.5 h-1.5 rounded-full ${
              apiStatus === 'ok' ? 'bg-green-500' :
              apiStatus === 'error' ? 'bg-red-500' : 'bg-yellow-400 animate-pulse'
            }`}/>
            {apiStatus === 'ok' ? t('Server connected', 'اتصال به سرور برقرار است') :
             apiStatus === 'error' ? t('Server unreachable — run bash scripts/dev.sh',
                                       'سرور در دسترس نیست — دستور bash scripts/dev.sh را اجرا کنید') :
             t('Connecting…', 'در حال اتصال…')}
          </div>
        </div>

        {/* Demo credentials hint */}
        <div className="mt-6 bg-white/10 rounded-xl p-4 text-xs text-blue-200">
          <p className="font-semibold text-blue-100 mb-2">{t('Demo credentials:', 'اطلاعات ورود نمونه:')}</p>
          {/* Credentials are Latin text: pinned LTR so bidi never reorders the
              punctuation in a password. */}
          <div dir="ltr" className="space-y-1 font-mono">
            <div className="flex justify-between">
              <span>admin</span><span className="text-blue-300">PharmPilot2024!</span>
            </div>
            <div className="flex justify-between">
              <span>pharmacist</span><span className="text-blue-300">Pharmacist2024!</span>
            </div>
            <div className="flex justify-between">
              <span>tech</span><span className="text-blue-300">Tech2024!</span>
            </div>
          </div>
        </div>

        <p className="text-center text-blue-400 text-xs mt-4">
          {t('PharmPilot AI v0.1 · Development Mode', 'پارم‌پایلوت هوشمند نسخهٔ ۰٫۱ · حالت توسعه')}
        </p>
      </div>
    </div>
  )
}
