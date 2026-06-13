/**
 * AnalyticsChat — #3 Natural Language Analytics ("Ask Your Data")
 * ===============================================================
 * A chat box that turns plain-language questions into safe SQL and a narrative
 * answer over the analytics tables. Shows the generated SQL (collapsible) and a
 * bar chart when the result is chartable.
 *
 * Consumes the §1.2 envelope; shows a TierBadge so the user knows whether the
 * answer came from the local or cloud brain.
 */
import { useState } from 'react'
import { useMutation } from '@tanstack/react-query'
import {
  BarChart, Bar, XAxis, YAxis, Tooltip, ResponsiveContainer, CartesianGrid,
} from 'recharts'
import { apiClient } from '../lib/api'
import TierBadge from './TierBadge'

interface ChartData { label_key: string; value_key: string; data: { label: string; value: number }[] }
interface AskResult {
  answer: string; sql: string | null; rows: Record<string, any>[]
  chart: ChartData | null; row_count?: number; blocked?: boolean; block_reason?: string
}
interface Envelope {
  result: AskResult; tier_used: 'local' | 'cloud' | 'hybrid'
  degraded: boolean; confidence: number; options_offline: string[]
}

interface Turn { question: string; env: Envelope }

const SAMPLE_QUESTIONS = [
  'Total revenue by drug last month',
  'How many controlled prescriptions this week?',
  'Top 10 drugs by dispense count',
  'Cash vs card payment totals',
]

export default function AnalyticsChat() {
  const [question, setQuestion] = useState('')
  const [turns, setTurns]       = useState<Turn[]>([])
  const [showSql, setShowSql]   = useState<number | null>(null)

  const askMutation = useMutation({
    mutationFn: (q: string) => apiClient.post('/intelligence/analytics/ask', { question: q })
                  .then(r => r.data as Envelope),
    onSuccess: (env, q) => { setTurns(t => [...t, { question: q, env }]); setQuestion('') },
  })

  const submit = (q: string) => {
    if (!q.trim() || askMutation.isPending) return
    askMutation.mutate(q.trim())
  }

  return (
    <div className="bg-[#1a1f2e] rounded-xl p-4 border border-[#1e293b] flex flex-col">
      <div className="flex items-center gap-2 mb-3">
        <span className="text-lg">💬</span>
        <h2 className="text-sm font-semibold text-slate-200">Ask Your Data</h2>
        <span className="text-[10px] text-slate-500">natural-language analytics</span>
      </div>

      {/* Conversation */}
      <div className="flex-1 space-y-3 max-h-72 overflow-y-auto mb-3">
        {turns.length === 0 && (
          <div className="space-y-2">
            <p className="text-xs text-slate-500">Try asking:</p>
            <div className="flex flex-wrap gap-1.5">
              {SAMPLE_QUESTIONS.map(q => (
                <button key={q} onClick={() => submit(q)}
                  className="text-[11px] px-2 py-1 rounded-full bg-slate-800 text-slate-300 hover:bg-slate-700 border border-slate-700">
                  {q}
                </button>
              ))}
            </div>
          </div>
        )}

        {turns.map((t, i) => {
          const r = t.env.result
          return (
            <div key={i} className="space-y-1.5">
              {/* Question */}
              <div className="text-xs text-slate-400">
                <span className="text-slate-500">Q:</span> {t.question}
              </div>
              {/* Answer */}
              <div className={`rounded-lg px-3 py-2 ${r.blocked ? 'bg-amber-950/40 border border-amber-900' : 'bg-slate-900 border border-slate-800'}`}>
                <div className="flex items-start justify-between gap-2">
                  <p className="text-sm text-slate-200">{r.answer}</p>
                  <TierBadge tier={t.env.tier_used} degraded={t.env.degraded}
                    optionsOffline={t.env.options_offline} compact />
                </div>

                {/* Chart */}
                {r.chart && r.chart.data.length > 0 && (
                  <div className="mt-2 h-40">
                    <ResponsiveContainer width="100%" height="100%">
                      <BarChart data={r.chart.data} margin={{ top: 4, right: 8, bottom: 4, left: 0 }}>
                        <CartesianGrid strokeDasharray="3 3" stroke="#1e293b" />
                        <XAxis dataKey="label" tick={{ fontSize: 9, fill: '#64748b' }} interval={0} angle={-25} textAnchor="end" height={50} />
                        <YAxis tick={{ fontSize: 9, fill: '#64748b' }} />
                        <Tooltip contentStyle={{ background: '#0f172a', border: '1px solid #1e293b', fontSize: 11 }} />
                        <Bar dataKey="value" fill="#3b82f6" radius={[3, 3, 0, 0]} />
                      </BarChart>
                    </ResponsiveContainer>
                  </div>
                )}

                {/* Row table fallback (small) */}
                {!r.chart && r.rows && r.rows.length > 0 && (
                  <div className="mt-2 overflow-x-auto">
                    <table className="text-[11px] text-slate-300 w-full">
                      <thead>
                        <tr className="text-slate-500">
                          {Object.keys(r.rows[0]).map(k => <th key={k} className="text-left pr-3 pb-1">{k}</th>)}
                        </tr>
                      </thead>
                      <tbody>
                        {r.rows.slice(0, 6).map((row, j) => (
                          <tr key={j}>
                            {Object.values(row).map((v, k) => <td key={k} className="pr-3 py-0.5 tabular-nums">{String(v)}</td>)}
                          </tr>
                        ))}
                      </tbody>
                    </table>
                    {r.row_count != null && r.row_count > 6 && (
                      <p className="text-[10px] text-slate-600 mt-1">…{r.row_count - 6} more rows</p>
                    )}
                  </div>
                )}

                {/* SQL toggle */}
                {r.sql && (
                  <div className="mt-1.5">
                    <button onClick={() => setShowSql(showSql === i ? null : i)}
                      className="text-[10px] text-slate-500 hover:text-slate-300">
                      {showSql === i ? '▾ hide SQL' : '▸ show SQL'}
                    </button>
                    {showSql === i && (
                      <pre className="text-[10px] text-emerald-300/80 bg-slate-950 rounded p-2 mt-1 overflow-x-auto whitespace-pre-wrap">{r.sql}</pre>
                    )}
                  </div>
                )}
              </div>
            </div>
          )
        })}

        {askMutation.isPending && (
          <p className="text-xs text-slate-500 animate-pulse">⌛ Thinking…</p>
        )}
      </div>

      {/* Input */}
      <div className="flex gap-2">
        <input
          value={question}
          onChange={e => setQuestion(e.target.value)}
          onKeyDown={e => { if (e.key === 'Enter') submit(question) }}
          placeholder="Ask about revenue, scripts, inventory, payments…"
          className="flex-1 bg-slate-900 border border-slate-700 rounded-lg px-3 py-2 text-sm text-slate-200 placeholder-slate-600 focus:outline-none focus:ring-1 focus:ring-blue-500"
        />
        <button
          onClick={() => submit(question)}
          disabled={askMutation.isPending || !question.trim()}
          className="px-4 py-2 bg-blue-600 text-white rounded-lg text-sm font-medium hover:bg-blue-500 disabled:opacity-40">
          Ask
        </button>
      </div>
    </div>
  )
}
