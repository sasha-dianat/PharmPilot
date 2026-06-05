/**
 * KnowledgeManager — Owner-facing Clinical Brain Training UI.
 * ============================================================
 * Replaces every `python scripts/import_references.py` terminal command
 * with a visual drag-and-drop interface the pharmacy owner can use.
 *
 * Five ingestion methods:
 *   1. File Drop  — drag & drop / browse; auto-detects format (PDF, DOCX, MD,
 *                   TXT, HTML, CSV, RTF, EPUB, JSON, SQLite .db)
 *   2. SQLite DB  — path to local/network .db file OR direct upload
 *   3. Website    — BFS crawler for a URL (same-domain, rate-limited)
 *   4. PubMed     — search queries → abstract ingestion
 *   5. Directory  — server-side folder path (batch ingest)
 *
 * Source Library shows every ingested source with chunk count + delete.
 * Stats bar shows total vector count in real time.
 */
import { useState, useRef, useCallback } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { apiClient } from '../lib/api'

// ── API helpers ───────────────────────────────────────────────────────────────
const kbApi = {
  stats: () => apiClient.get('/knowledge/stats'),
  sources: () => apiClient.get('/knowledge/sources'),
  deleteSource: (id: string) => apiClient.delete(`/knowledge/sources/${id}`),
  uploadFile: (file: File, language: string, collection: string) => {
    const form = new FormData()
    form.append('file', file)
    form.append('language', language)
    form.append('collection', collection)
    return apiClient.post('/knowledge/ingest/file', form, {
      headers: { 'Content-Type': 'multipart/form-data' },
      timeout: 120_000,
    })
  },
  uploadSqlite: (file: File, language: string, collection: string, tablesFilter: string) => {
    const form = new FormData()
    form.append('file', file)
    form.append('language', language)
    form.append('collection', collection)
    if (tablesFilter) form.append('tables_filter', tablesFilter)
    return apiClient.post('/knowledge/ingest/sqlite/upload', form, {
      headers: { 'Content-Type': 'multipart/form-data' },
      timeout: 180_000,
    })
  },
  sqlitePath: (body: object) => apiClient.post('/knowledge/ingest/sqlite', body),
  crawlUrl: (body: object) => apiClient.post('/knowledge/ingest/crawl', body),
  ingestText: (body: object) => apiClient.post('/knowledge/ingest/text', body),
  ingestDirectory: (body: object) => apiClient.post('/knowledge/ingest/directory', body),
}

// ── Types ─────────────────────────────────────────────────────────────────────
interface KBStats {
  total_vectors: number
  status: string
  message?: string
}
interface KBSource {
  source_id: string
  title: string
  source_type: string
  language: string
  collection: string
  chunk_count: number
  url?: string
  file_path?: string
}
interface JobStatus {
  id: string
  label: string
  status: 'running' | 'done' | 'error'
  chunks?: number
  error?: string
}

const COLLECTION_OPTIONS = [
  { value: 'owner_references',     label: 'Owner References' },
  { value: 'iranian_pharmacopeia', label: 'داروپرسی ایران (Iranian Pharmacopeia)' },
  { value: 'guidelines',           label: 'Clinical Guidelines' },
  { value: 'drug_interactions',    label: 'Drug Interactions' },
  { value: 'dosing',               label: 'Dosing References' },
]
const TYPE_ICON: Record<string, string> = {
  owner_reference: '📄', pubmed_article: '🧬', clinical_guideline: '📋',
  fda_drug_label: '💊', web_crawl: '🌐', markdown: '📝',
  csv_table: '📊', rtf_document: '📑', epub_book: '📚', json_data: '{}',
}
const ACCEPT_TYPES = '.pdf,.docx,.doc,.md,.markdown,.txt,.text,.html,.htm,.xhtml,.csv,.tsv,.rtf,.epub,.json,.jsonl,.sqlite,.sqlite3,.db,.db3'

// ── Main component ────────────────────────────────────────────────────────────
export default function KnowledgeManager() {
  const qc = useQueryClient()
  const [activeTab, setActiveTab] = useState<'files' | 'sqlite' | 'web' | 'pubmed' | 'directory'>('files')
  const [language, setLanguage] = useState('fa')
  const [collection, setCollection] = useState('owner_references')
  const [jobs, setJobs] = useState<JobStatus[]>([])
  const [dragOver, setDragOver] = useState(false)
  const fileInputRef = useRef<HTMLInputElement>(null)

  const { data: stats }   = useQuery<KBStats>({ queryKey: ['kb-stats'], queryFn: () => kbApi.stats().then(r => r.data), refetchInterval: 10_000 })
  const { data: srcData } = useQuery<{ sources: KBSource[] }>({ queryKey: ['kb-sources'], queryFn: () => kbApi.sources().then(r => r.data), refetchInterval: 30_000 })
  const deleteMut = useMutation({ mutationFn: (id: string) => kbApi.deleteSource(id), onSuccess: () => qc.invalidateQueries({ queryKey: ['kb-sources', 'kb-stats'] }) })

  const addJob = (id: string, label: string): void =>
    setJobs(prev => [{ id, label, status: 'running' }, ...prev.slice(0, 19)])
  const finishJob = (id: string, chunks: number) =>
    setJobs(prev => prev.map(j => j.id === id ? { ...j, status: 'done', chunks } : j))
  const failJob = (id: string, error: string) =>
    setJobs(prev => prev.map(j => j.id === id ? { ...j, status: 'error', error } : j))

  const handleFiles = useCallback(async (files: FileList | File[]) => {
    const arr = Array.from(files)
    for (const file of arr) {
      const jid = Math.random().toString(36).slice(2)
      const isSqlite = /\.(sqlite|sqlite3|db|db3)$/i.test(file.name)
      addJob(jid, file.name)
      try {
        const { data } = isSqlite
          ? await kbApi.uploadSqlite(file, language, collection, '')
          : await kbApi.uploadFile(file, language, collection)
        finishJob(jid, data.chunks_stored ?? 0)
        qc.invalidateQueries({ queryKey: ['kb-stats', 'kb-sources'] })
      } catch (e: any) {
        failJob(jid, e?.response?.data?.detail ?? 'Upload failed')
      }
    }
  }, [language, collection, qc])

  const totalVectors = stats?.total_vectors ?? 0
  const sources = srcData?.sources ?? []

  return (
    <div className="flex flex-col h-full bg-[#0f1117] text-slate-100 overflow-hidden">

      {/* ── Stats header ───────────────────────────────────────────────── */}
      <div className="flex-shrink-0 px-6 py-4 border-b border-[#1e293b] flex items-center gap-6">
        <div>
          <div className="text-xs text-slate-500 uppercase tracking-widest">Knowledge Base</div>
          <div className="text-2xl font-bold text-blue-400">{totalVectors.toLocaleString()}</div>
          <div className="text-xs text-slate-500">indexed passages</div>
        </div>
        <div className="h-10 w-px bg-[#1e293b]" />
        <div>
          <div className="text-xs text-slate-500">Sources</div>
          <div className="text-xl font-bold text-emerald-400">{sources.length}</div>
        </div>
        <div className="h-10 w-px bg-[#1e293b]" />
        <div className="flex-1">
          <div className="h-2 bg-[#1e293b] rounded-full overflow-hidden">
            <div
              className="h-full bg-gradient-to-r from-blue-500 to-emerald-500 rounded-full transition-all"
              style={{ width: `${Math.min((totalVectors / 10000) * 100, 100)}%` }}
            />
          </div>
          <div className="text-[10px] text-slate-500 mt-0.5">{Math.round(totalVectors / 100) / 10}k / 10k target</div>
        </div>
        <div className={`text-xs px-2 py-1 rounded font-medium ${stats?.status === 'operational' ? 'bg-green-900/40 text-green-400' : 'bg-yellow-900/40 text-yellow-400'}`}>
          {stats?.status ?? 'connecting…'}
        </div>
      </div>

      <div className="flex flex-1 overflow-hidden">

        {/* ── Left: Ingestion panel ─────────────────────────────────────── */}
        <div className="w-[420px] flex-shrink-0 flex flex-col border-r border-[#1e293b] overflow-hidden">

          {/* Language + Collection selectors */}
          <div className="flex-shrink-0 px-4 pt-3 pb-2 border-b border-[#1e293b] flex gap-3">
            <div className="flex-1">
              <label className="text-[10px] text-slate-500 uppercase mb-1 block">Language</label>
              <select value={language} onChange={e => setLanguage(e.target.value)}
                className="w-full bg-[#1a1f2e] border border-[#2d3748] text-slate-200 text-xs rounded px-2 py-1.5">
                <option value="fa">فارسی (Persian)</option>
                <option value="en">English</option>
                <option value="ar">عربی (Arabic)</option>
              </select>
            </div>
            <div className="flex-1">
              <label className="text-[10px] text-slate-500 uppercase mb-1 block">Collection</label>
              <select value={collection} onChange={e => setCollection(e.target.value)}
                className="w-full bg-[#1a1f2e] border border-[#2d3748] text-slate-200 text-xs rounded px-2 py-1.5">
                {COLLECTION_OPTIONS.map(o => <option key={o.value} value={o.value}>{o.label}</option>)}
              </select>
            </div>
          </div>

          {/* Tab selector */}
          <div className="flex-shrink-0 flex border-b border-[#1e293b] text-[10px]">
            {([
              { id: 'files', label: '📁 Files', title: 'Upload files' },
              { id: 'sqlite', label: '🗄 SQLite', title: 'Database file' },
              { id: 'web', label: '🌐 Web', title: 'Crawl website' },
              { id: 'pubmed', label: '🧬 PubMed', title: 'Search PubMed' },
              { id: 'directory', label: '📂 Dir', title: 'Server directory' },
            ] as const).map(t => (
              <button key={t.id} onClick={() => setActiveTab(t.id)} title={t.title}
                className={`flex-1 py-2 transition-colors ${activeTab === t.id ? 'text-blue-400 border-b-2 border-blue-500' : 'text-slate-500 hover:text-slate-300'}`}>
                {t.label}
              </button>
            ))}
          </div>

          {/* Tab content */}
          <div className="flex-1 overflow-y-auto p-4">
            {activeTab === 'files'     && <FilesTab language={language} collection={collection} onFiles={handleFiles} dragOver={dragOver} setDragOver={setDragOver} fileInputRef={fileInputRef} />}
            {activeTab === 'sqlite'    && <SqliteTab language={language} collection={collection} jobs={jobs} addJob={addJob} finishJob={finishJob} failJob={failJob} invalidate={() => qc.invalidateQueries({ queryKey: ['kb-stats','kb-sources'] })} />}
            {activeTab === 'web'       && <WebTab language={language} collection={collection} addJob={addJob} finishJob={finishJob} failJob={failJob} invalidate={() => qc.invalidateQueries({ queryKey: ['kb-stats','kb-sources'] })} />}
            {activeTab === 'pubmed'    && <PubMedTab language={language} collection={collection} addJob={addJob} finishJob={finishJob} failJob={failJob} invalidate={() => qc.invalidateQueries({ queryKey: ['kb-stats','kb-sources'] })} />}
            {activeTab === 'directory' && <DirectoryTab language={language} collection={collection} addJob={addJob} finishJob={finishJob} failJob={failJob} invalidate={() => qc.invalidateQueries({ queryKey: ['kb-stats','kb-sources'] })} />}
          </div>

          {/* Job log */}
          {jobs.length > 0 && (
            <div className="flex-shrink-0 border-t border-[#1e293b] p-3 space-y-1 max-h-48 overflow-y-auto">
              <div className="text-[9px] text-slate-500 uppercase tracking-widest mb-1">Recent Jobs</div>
              {jobs.map(j => (
                <div key={j.id} className="flex items-center gap-2 text-[10px]">
                  {j.status === 'running' && <div className="w-2.5 h-2.5 border border-blue-400 border-t-transparent rounded-full animate-spin flex-shrink-0" />}
                  {j.status === 'done'    && <span className="text-green-400 flex-shrink-0">✓</span>}
                  {j.status === 'error'   && <span className="text-red-400 flex-shrink-0">✗</span>}
                  <span className="truncate flex-1 text-slate-300">{j.label}</span>
                  {j.status === 'done'  && <span className="text-slate-500 flex-shrink-0">{j.chunks} chunks</span>}
                  {j.status === 'error' && <span className="text-red-400 truncate max-w-[100px]">{j.error}</span>}
                </div>
              ))}
            </div>
          )}
        </div>

        {/* ── Right: Source library ─────────────────────────────────────── */}
        <div className="flex-1 flex flex-col overflow-hidden">
          <div className="flex-shrink-0 px-5 py-3 border-b border-[#1e293b] flex items-center justify-between">
            <div className="text-sm font-semibold text-slate-200">📚 Ingested Sources</div>
            <div className="text-xs text-slate-500">{sources.length} sources · {totalVectors.toLocaleString()} passages total</div>
          </div>
          <div className="flex-1 overflow-y-auto p-4 space-y-2">
            {sources.length === 0 ? (
              <div className="flex flex-col items-center justify-center h-full text-slate-600 text-center">
                <div className="text-5xl mb-3">📭</div>
                <div className="text-sm">Knowledge base is empty</div>
                <div className="text-xs mt-1">Upload documents or run PubMed search to start training</div>
              </div>
            ) : (
              sources.map(s => (
                <div key={s.source_id} className="flex items-center gap-3 bg-[#1a1f2e] border border-[#2d3748] rounded-lg px-3 py-2.5 group">
                  <span className="text-lg flex-shrink-0">{TYPE_ICON[s.source_type] ?? '📄'}</span>
                  <div className="flex-1 min-w-0">
                    <div className="text-xs font-medium text-slate-200 truncate">{s.title}</div>
                    <div className="text-[9px] text-slate-500 flex gap-2 mt-0.5 flex-wrap">
                      <span>{s.source_type}</span>
                      <span>·</span>
                      <span className="capitalize">{s.language}</span>
                      <span>·</span>
                      <span>{s.collection}</span>
                      {s.url && <><span>·</span><span className="truncate max-w-[120px]">{s.url}</span></>}
                    </div>
                  </div>
                  <div className="flex items-center gap-2 flex-shrink-0">
                    <div className="text-right">
                      <div className="text-xs font-bold text-blue-400">{(s.chunk_count || 0).toLocaleString()}</div>
                      <div className="text-[9px] text-slate-600">passages</div>
                    </div>
                    <button
                      onClick={() => { if (confirm(`Remove "${s.title}"?`)) deleteMut.mutate(s.source_id) }}
                      className="opacity-0 group-hover:opacity-100 text-red-500 hover:text-red-400 text-sm px-1"
                      title="Remove source"
                    >
                      ×
                    </button>
                  </div>
                </div>
              ))
            )}
          </div>
        </div>
      </div>
    </div>
  )
}

// ── File Drop Tab ─────────────────────────────────────────────────────────────
function FilesTab({ language, collection, onFiles, dragOver, setDragOver, fileInputRef }: {
  language: string; collection: string
  onFiles: (files: FileList | File[]) => void
  dragOver: boolean; setDragOver: (v: boolean) => void
  fileInputRef: React.RefObject<HTMLInputElement>
}) {
  return (
    <div className="space-y-3">
      <div
        onDragOver={e => { e.preventDefault(); setDragOver(true) }}
        onDragLeave={() => setDragOver(false)}
        onDrop={e => { e.preventDefault(); setDragOver(false); onFiles(e.dataTransfer.files) }}
        onClick={() => fileInputRef.current?.click()}
        className={`border-2 border-dashed rounded-xl p-8 text-center cursor-pointer transition-colors ${
          dragOver ? 'border-blue-400 bg-blue-900/20' : 'border-[#2d3748] hover:border-slate-500 hover:bg-[#1a1f2e]'
        }`}
      >
        <div className="text-4xl mb-2">📂</div>
        <div className="text-sm text-slate-300 font-medium">Drop files here or click to browse</div>
        <div className="text-xs text-slate-500 mt-1">Multiple files supported — all formats auto-detected</div>
      </div>
      <input
        ref={fileInputRef}
        type="file"
        multiple
        accept={ACCEPT_TYPES}
        className="hidden"
        onChange={e => { if (e.target.files) onFiles(e.target.files) }}
      />
      <div className="grid grid-cols-4 gap-1 text-[9px] text-center text-slate-500">
        {['PDF', 'DOCX', 'MD', 'TXT', 'HTML', 'CSV', 'RTF', 'EPUB', 'JSON', 'SQLite', '.db', '.db3'].map(f => (
          <span key={f} className="bg-[#1a1f2e] border border-[#2d3748] rounded px-1.5 py-1">{f}</span>
        ))}
      </div>
      <p className="text-[10px] text-slate-600 text-center">
        SQLite/DB files are auto-detected — all tables extracted automatically
      </p>
    </div>
  )
}

// ── SQLite Tab ────────────────────────────────────────────────────────────────
function SqliteTab({ language, collection, jobs, addJob, finishJob, failJob, invalidate }: {
  language: string; collection: string; jobs: JobStatus[]
  addJob: Function; finishJob: Function; failJob: Function; invalidate: Function
}) {
  const [mode, setMode] = useState<'path' | 'upload'>('path')
  const [dbPath, setDbPath] = useState('')
  const [tables, setTables] = useState('')
  const [loading, setLoading] = useState(false)
  const fileRef = useRef<HTMLInputElement>(null)

  const handlePath = async () => {
    if (!dbPath.trim()) return
    const jid = Math.random().toString(36).slice(2)
    addJob(jid, dbPath.split('/').pop() || dbPath)
    setLoading(true)
    try {
      const { data } = await kbApi.sqlitePath({ db_path: dbPath, tables: tables ? tables.split(',').map(t=>t.trim()) : null, language, collection })
      finishJob(jid, data.chunks_stored ?? 0)
      invalidate()
    } catch (e: any) {
      failJob(jid, e?.response?.data?.detail ?? 'Failed')
    } finally { setLoading(false) }
  }

  const handleUpload = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0]
    if (!file) return
    const jid = Math.random().toString(36).slice(2)
    addJob(jid, file.name)
    setLoading(true)
    try {
      const { data } = await kbApi.uploadSqlite(file, language, collection, tables)
      finishJob(jid, data.chunks_stored ?? 0)
      invalidate()
    } catch (e: any) {
      failJob(jid, e?.response?.data?.detail ?? 'Failed')
    } finally { setLoading(false); if (fileRef.current) fileRef.current.value = '' }
  }

  return (
    <div className="space-y-4">
      <div className="flex gap-2">
        <button onClick={() => setMode('path')} className={`flex-1 py-1.5 text-xs rounded border transition-colors ${mode==='path' ? 'border-blue-500 bg-blue-900/30 text-blue-300' : 'border-[#2d3748] text-slate-500'}`}>🔗 Network/Local Path</button>
        <button onClick={() => setMode('upload')} className={`flex-1 py-1.5 text-xs rounded border transition-colors ${mode==='upload' ? 'border-blue-500 bg-blue-900/30 text-blue-300' : 'border-[#2d3748] text-slate-500'}`}>⬆ Upload File</button>
      </div>

      {mode === 'path' ? (
        <div className="space-y-3">
          <div>
            <label className="text-[10px] text-slate-500 mb-1 block">Database path (local or mounted network share)</label>
            <input value={dbPath} onChange={e => setDbPath(e.target.value)} placeholder="/mnt/nas/formulary.db  or  //server/share/drugs.sqlite"
              className="w-full bg-[#1a1f2e] border border-[#2d3748] text-slate-200 text-xs rounded px-3 py-2 focus:outline-none focus:border-blue-500" />
          </div>
          <div>
            <label className="text-[10px] text-slate-500 mb-1 block">Tables to extract (blank = all tables)</label>
            <input value={tables} onChange={e => setTables(e.target.value)} placeholder="drugs, interactions, dosing"
              className="w-full bg-[#1a1f2e] border border-[#2d3748] text-slate-200 text-xs rounded px-3 py-2 focus:outline-none focus:border-blue-500" />
          </div>
          <button onClick={handlePath} disabled={!dbPath.trim() || loading}
            className="w-full py-2 bg-blue-600 hover:bg-blue-700 text-white text-xs rounded font-medium disabled:opacity-40">
            {loading ? 'Extracting…' : '🗄 Extract from Database'}
          </button>
          <p className="text-[10px] text-slate-600">
            The file must be accessible on the server. For SMB/NFS shares, ensure they are mounted before ingestion.
            All text columns are extracted; binary BLOBs are skipped automatically.
          </p>
        </div>
      ) : (
        <div className="space-y-3">
          <div>
            <label className="text-[10px] text-slate-500 mb-1 block">Tables to extract (blank = all)</label>
            <input value={tables} onChange={e => setTables(e.target.value)} placeholder="drugs, interactions"
              className="w-full bg-[#1a1f2e] border border-[#2d3748] text-slate-200 text-xs rounded px-3 py-2 focus:outline-none focus:border-blue-500" />
          </div>
          <button onClick={() => fileRef.current?.click()} disabled={loading}
            className="w-full py-6 border-2 border-dashed border-[#2d3748] hover:border-blue-500 rounded-xl text-slate-400 text-sm hover:text-slate-200 transition-colors">
            {loading ? '⏳ Uploading…' : '📤 Click to upload .db / .sqlite file'}
          </button>
          <input ref={fileRef} type="file" accept=".sqlite,.sqlite3,.db,.db3" className="hidden" onChange={handleUpload} />
        </div>
      )}
    </div>
  )
}

// ── Web Crawler Tab ───────────────────────────────────────────────────────────
function WebTab({ language, collection, addJob, finishJob, failJob, invalidate }: any) {
  const [url, setUrl] = useState('')
  const [maxPages, setMaxPages] = useState(100)
  const [sameDomain, setSameDomain] = useState(true)
  const [loading, setLoading] = useState(false)

  const handle = async () => {
    if (!url.trim()) return
    const jid = Math.random().toString(36).slice(2)
    addJob(jid, url)
    setLoading(true)
    try {
      const { data } = await kbApi.crawlUrl({ start_url: url, max_pages: maxPages, same_domain_only: sameDomain, language, collection })
      finishJob(jid, data.ingested ?? 0)
      invalidate()
    } catch (e: any) { failJob(jid, e?.response?.data?.detail ?? 'Crawl failed') }
    finally { setLoading(false) }
  }

  return (
    <div className="space-y-3">
      <div>
        <label className="text-[10px] text-slate-500 mb-1 block">Website URL</label>
        <input value={url} onChange={e => setUrl(e.target.value)} placeholder="https://formulary.example.ir/"
          className="w-full bg-[#1a1f2e] border border-[#2d3748] text-slate-200 text-xs rounded px-3 py-2 focus:outline-none focus:border-blue-500" />
      </div>
      <div className="flex gap-3">
        <div className="flex-1">
          <label className="text-[10px] text-slate-500 mb-1 block">Max pages</label>
          <input type="number" value={maxPages} onChange={e => setMaxPages(+e.target.value)} min={1} max={2000}
            className="w-full bg-[#1a1f2e] border border-[#2d3748] text-slate-200 text-xs rounded px-3 py-2 focus:outline-none focus:border-blue-500" />
        </div>
        <div className="flex items-end pb-2">
          <label className="flex items-center gap-2 text-xs text-slate-400 cursor-pointer">
            <input type="checkbox" checked={sameDomain} onChange={e => setSameDomain(e.target.checked)} className="rounded" />
            Same domain only
          </label>
        </div>
      </div>
      <button onClick={handle} disabled={!url.trim() || loading}
        className="w-full py-2 bg-indigo-600 hover:bg-indigo-700 text-white text-xs rounded font-medium disabled:opacity-40">
        {loading ? `Crawling… (rate-limited, may take time)` : '🌐 Start Crawl'}
      </button>
      <p className="text-[10px] text-slate-600">Crawling is rate-limited (1 req/s) to be polite to public sites.</p>
    </div>
  )
}

// ── PubMed Tab ────────────────────────────────────────────────────────────────
const DEFAULT_QUERIES = [
  "drug interaction clinical pharmacology",
  "opioid benzodiazepine respiratory depression",
  "metformin renal impairment lactic acidosis",
  "beers criteria elderly medications",
  "G6PD deficiency oxidant drugs hemolysis",
]

function PubMedTab({ language, collection, addJob, finishJob, failJob, invalidate }: any) {
  const [query, setQuery] = useState('')
  const [maxResults, setMaxResults] = useState(100)
  const [loading, setLoading] = useState(false)

  const handle = async (q: string) => {
    if (!q.trim()) return
    const jid = Math.random().toString(36).slice(2)
    addJob(jid, `PubMed: ${q.slice(0, 40)}`)
    setLoading(true)
    try {
      const { data } = await apiClient.post('/knowledge/ingest/pubmed', { query: q, max_results: maxResults, language, collection })
      finishJob(jid, data.chunks_stored ?? 0)
      invalidate()
    } catch (e: any) { failJob(jid, e?.response?.data?.detail ?? 'PubMed failed') }
    finally { setLoading(false) }
  }

  return (
    <div className="space-y-3">
      <div>
        <label className="text-[10px] text-slate-500 mb-1 block">Search query</label>
        <div className="flex gap-2">
          <input value={query} onChange={e => setQuery(e.target.value)} onKeyDown={e => e.key === 'Enter' && handle(query)}
            placeholder="metformin renal contraindication…"
            className="flex-1 bg-[#1a1f2e] border border-[#2d3748] text-slate-200 text-xs rounded px-3 py-2 focus:outline-none focus:border-blue-500" />
          <input type="number" value={maxResults} onChange={e => setMaxResults(+e.target.value)} min={10} max={500}
            title="Max articles"
            className="w-16 bg-[#1a1f2e] border border-[#2d3748] text-slate-200 text-xs rounded px-2 py-2 focus:outline-none focus:border-blue-500" />
        </div>
      </div>
      <button onClick={() => handle(query)} disabled={!query.trim() || loading}
        className="w-full py-2 bg-teal-600 hover:bg-teal-700 text-white text-xs rounded font-medium disabled:opacity-40">
        {loading ? 'Fetching abstracts…' : '🧬 Search & Ingest'}
      </button>
      <div className="space-y-1">
        <div className="text-[10px] text-slate-500 uppercase mb-1">Quick queries (clinical):</div>
        {DEFAULT_QUERIES.map(q => (
          <button key={q} onClick={() => handle(q)} disabled={loading}
            className="w-full text-left px-2 py-1.5 text-[10px] bg-[#1a1f2e] border border-[#2d3748] rounded hover:border-teal-500 text-slate-400 hover:text-slate-200 truncate">
            🧬 {q}
          </button>
        ))}
      </div>
    </div>
  )
}

// ── Directory Tab ─────────────────────────────────────────────────────────────
function DirectoryTab({ language, collection, addJob, finishJob, failJob, invalidate }: any) {
  const [dirPath, setDirPath] = useState('')
  const [recursive, setRecursive] = useState(true)
  const [loading, setLoading] = useState(false)

  const handle = async () => {
    if (!dirPath.trim()) return
    const jid = Math.random().toString(36).slice(2)
    addJob(jid, `Directory: ${dirPath.split('/').pop()}`)
    setLoading(true)
    try {
      const { data } = await kbApi.ingestDirectory({ directory: dirPath, recursive, language, collection })
      finishJob(jid, data.ingested ?? 0)
      invalidate()
    } catch (e: any) { failJob(jid, e?.response?.data?.detail ?? 'Directory failed') }
    finally { setLoading(false) }
  }

  return (
    <div className="space-y-3">
      <div>
        <label className="text-[10px] text-slate-500 mb-1 block">Directory path (server-side)</label>
        <input value={dirPath} onChange={e => setDirPath(e.target.value)} placeholder="/home/owner/references/"
          className="w-full bg-[#1a1f2e] border border-[#2d3748] text-slate-200 text-xs rounded px-3 py-2 focus:outline-none focus:border-blue-500" />
      </div>
      <label className="flex items-center gap-2 text-xs text-slate-400 cursor-pointer">
        <input type="checkbox" checked={recursive} onChange={e => setRecursive(e.target.checked)} />
        Include subdirectories
      </label>
      <button onClick={handle} disabled={!dirPath.trim() || loading}
        className="w-full py-2 bg-violet-600 hover:bg-violet-700 text-white text-xs rounded font-medium disabled:opacity-40">
        {loading ? 'Scanning directory…' : '📂 Ingest All Files'}
      </button>
      <p className="text-[10px] text-slate-600">
        The directory must be accessible on the API server. All 16 supported formats
        (PDF, DOCX, MD, TXT, HTML, CSV, RTF, EPUB, JSON, SQLite, …) are processed automatically.
      </p>
    </div>
  )
}
