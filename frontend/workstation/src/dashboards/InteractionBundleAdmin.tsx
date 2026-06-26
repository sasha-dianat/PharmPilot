import { useRef, useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { clinicalApi } from '../lib/api'

interface Status { installed: boolean; stats: Record<string, string> }

export default function InteractionBundleAdmin() {
  const qc = useQueryClient()
  const fileRef = useRef<HTMLInputElement>(null)
  const [msg, setMsg] = useState<{ kind: 'ok' | 'err'; text: string } | null>(null)
  const [busy, setBusy] = useState(false)

  const { data } = useQuery<Status>({
    queryKey: ['bundle-status'],
    queryFn: () => clinicalApi.getBundleStatus().then(r => r.data),
  })
  const stats = data?.stats ?? {}

  const install = async () => {
    const file = fileRef.current?.files?.[0]
    if (!file) { setMsg({ kind: 'err', text: 'Pick a .sqlite bundle first.' }); return }
    if (data?.installed &&
        !window.confirm(`This replaces the current bundle (${stats.schema_version ?? '?'}). Continue?`)) return
    setBusy(true); setMsg(null)
    try {
      const { data: res } = await clinicalApi.installBundle(file, data?.installed ?? false)
      setMsg({ kind: 'ok', text: `Installed: ${res.stats.rule_count ?? '?'} rules, ${res.stats.attribute_count ?? '?'} attributes.` })
      qc.invalidateQueries({ queryKey: ['bundle-status'] })
    } catch (e: unknown) {
      const detail = (e as { response?: { data?: { detail?: string } } })?.response?.data?.detail
      setMsg({ kind: 'err', text: detail || 'Install failed.' })
    } finally { setBusy(false) }
  }

  return (
    <div className="p-4 space-y-4 text-slate-100">
      <h2 className="text-lg font-bold">Interaction Knowledge Bundle</h2>

      <div className="bg-slate-800/50 border border-slate-700 rounded-lg p-4 space-y-1 text-sm">
        <p className="font-semibold mb-1">Current bundle</p>
        {data?.installed ? (
          <>
            <p>Schema: {stats.schema_version}</p>
            <p>Datasets: {stats.datasets ?? '—'}</p>
            <p>Rules: {stats.rule_count ?? '—'} · Attributes: {stats.attribute_count ?? '—'}</p>
            <p>Built: {stats.built_at ?? '—'}</p>
            {stats.checksum && <p className="text-slate-500">checksum {stats.checksum.slice(0, 12)}…</p>}
          </>
        ) : (
          <p className="text-slate-400">No bundle installed — the engine is running on curated rules only.</p>
        )}
      </div>

      <div className="bg-slate-800/50 border border-slate-700 rounded-lg p-4 space-y-3">
        <p className="font-semibold text-sm">Install a bundle (.sqlite from the Colab ingestion notebook)</p>
        <input ref={fileRef} type="file" accept=".sqlite,.db,.sqlite3"
          className="text-sm text-slate-300" />
        <button onClick={install} disabled={busy}
          className="block px-4 py-2 bg-indigo-600 text-white text-sm rounded-lg disabled:opacity-50">
          {busy ? 'Installing…' : 'Install bundle'}
        </button>
        {msg && <p className={`text-sm ${msg.kind === 'ok' ? 'text-emerald-400' : 'text-red-400'}`}>{msg.text}</p>}
      </div>
    </div>
  )
}
