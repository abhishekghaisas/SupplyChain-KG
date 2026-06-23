import { useState } from 'react'
import { Brain, CheckCircle, XCircle } from 'lucide-react'
import { reasoning as api } from '../api/client'
import { ErrorBanner, Spinner, MonoId } from '../components/ui'
import SearchBar from '../components/SearchBar'

// ── ID input with inline search ──────────────────────────────────────────────
// Wraps SearchBar — clicking a result fills the field.

function IdInput({ label, value, onChange, entityType, placeholder }) {
  const [showResults, setShowResults] = useState(false)
  const [results,     setResults]     = useState([])

  function handleResults(r, q) {
    setResults(r)
    setShowResults(r.length > 0 && q.length > 0)
  }

  function pick(id) {
    onChange(id)
    setShowResults(false)
    setResults([])
  }

  return (
    <div className="relative">
      <label className="field-label">{label}</label>
      <SearchBar
        entityType={entityType}
        placeholder={placeholder}
        onResults={handleResults}
      />
      {value && (
        <div className="mt-1 flex items-center gap-1.5">
          <span className="text-xs text-slate-400">Selected:</span>
          <span className="font-mono text-xs text-slate-700 bg-slate-100 px-1.5 py-0.5 rounded">{value}</span>
          <button onClick={() => onChange('')} className="text-xs text-slate-400 hover:text-slate-600">×</button>
        </div>
      )}
      {showResults && (
        <div className="absolute z-20 top-full left-0 right-0 mt-1 bg-white border
                        border-slate-200 rounded shadow-lg max-h-48 overflow-y-auto">
          {results.map(r => (
            <button key={r.entity_id} onClick={() => pick(r.entity_id)}
              className="w-full flex items-center gap-2 px-3 py-2 text-left
                         hover:bg-slate-50 border-b border-slate-100 last:border-0">
              <span className="font-mono text-xs text-slate-500 w-28 shrink-0 truncate">{r.entity_id}</span>
              <span className="text-sm text-slate-700 truncate">{r.name}</span>
              {r.score !== null && (
                <span className="font-mono text-xs text-slate-300 ml-auto shrink-0">{r.score?.toFixed(2)}</span>
              )}
            </button>
          ))}
        </div>
      )}
    </div>
  )
}

// ── Result card ───────────────────────────────────────────────────────────────

function RuleResult({ result, provenance }) {
  const [showProvenance, setShowProvenance] = useState(false)

  if (!result) return null

  return (
    <div className={`panel mt-4 border-l-4 ${result.passed ? 'border-l-green-500' : 'border-l-red-500'}`}>
      <div className="px-4 py-3">
        <div className="flex items-center gap-2 mb-2">
          {result.passed
            ? <CheckCircle size={15} className="text-green-500 shrink-0" />
            : <XCircle    size={15} className="text-red-500 shrink-0" />}
          <span className={`text-sm font-medium ${result.passed ? 'text-green-700' : 'text-red-700'}`}>
            {result.passed ? 'Pass' : 'Fail'}
          </span>
          <span className="text-xs text-slate-400 font-mono ml-auto">{result.rule_name}</span>
        </div>

        <p className="text-sm text-slate-700 mb-2">{result.reason}</p>

        <div className="flex items-center gap-4 text-xs text-slate-400">
          <span>Confidence: <span className="font-mono text-slate-600">{(result.confidence * 100).toFixed(0)}%</span></span>
          <span>Severity: <span className="font-mono text-slate-600">{result.failure_severity}</span></span>
        </div>

        {result.facts_used?.length > 0 && (
          <div className="mt-2 flex flex-wrap gap-1">
            {result.facts_used.map(f => (
              <span key={f} className="font-mono text-xs bg-slate-100 text-slate-500 px-1.5 py-0.5 rounded">{f}</span>
            ))}
          </div>
        )}

        {provenance && (
          <div className="mt-3">
            <button onClick={() => setShowProvenance(v => !v)}
              className="text-xs text-blue-600 hover:underline">
              {showProvenance ? 'Hide' : 'Show'} provenance chain ({provenance.total_entries} entries)
            </button>
            {showProvenance && (
              <div className="mt-2 space-y-1.5">
                {provenance.timeline?.map((e, i) => (
                  <div key={i} className="flex items-start gap-2 text-xs">
                    <span className="font-mono text-slate-400 shrink-0 w-20 text-right">
                      {e.type.toLowerCase()}
                    </span>
                    <span className="text-slate-600">{e.description}</span>
                  </div>
                ))}
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  )
}

// ── Compatibility check ───────────────────────────────────────────────────────

function CompatibilityCheck() {
  const [originalId,   setOriginalId]   = useState('')
  const [substituteId, setSubstituteId] = useState('')
  const [result,       setResult]       = useState(null)
  const [error,        setError]        = useState('')
  const [loading,      setLoading]      = useState(false)

  async function run(e) {
    e.preventDefault(); setLoading(true); setError(''); setResult(null)
    try {
      setResult(await api.compatibility({
        original_part_id:   originalId,
        substitute_part_id: substituteId,
      }))
    } catch (err) { setError(err.message) }
    finally { setLoading(false) }
  }

  return (
    <div>
      <h2 className="text-xs font-semibold text-slate-500 uppercase tracking-wider mb-3">
        Part Compatibility Check
      </h2>
      <form onSubmit={run} className="flex gap-2 items-start">
        <div className="flex-1">
          <IdInput
            label="Original Part ID"
            value={originalId}
            onChange={setOriginalId}
            entityType="part"
            placeholder="Search or enter P-12345"
          />
        </div>
        <div className="flex-1">
          <IdInput
            label="Substitute Part ID"
            value={substituteId}
            onChange={setSubstituteId}
            entityType="part"
            placeholder="Search or enter P-67890"
          />
        </div>
        <button type="submit" className="btn-primary self-end" disabled={loading || !originalId || !substituteId}>
          {loading ? <Spinner size={13} /> : 'Check'}
        </button>
      </form>
      <ErrorBanner message={error} onDismiss={() => setError('')} />
      {result && (
        <RuleResult result={result.result} provenance={result.provenance} />
      )}
    </div>
  )
}

// ── Lead time check ───────────────────────────────────────────────────────────

function LeadTimeCheck() {
  const today = new Date().toISOString().split('T')[0]
  const [leadDays,   setLeadDays]   = useState('')
  const [required,   setRequired]   = useState('')
  const [result,     setResult]     = useState(null)
  const [error,      setError]      = useState('')
  const [loading,    setLoading]    = useState(false)

  async function run(e) {
    e.preventDefault(); setLoading(true); setError(''); setResult(null)
    try {
      setResult(await api.leadTime({
        supplier_lead_time_days: Number(leadDays),
        required_date: required,
      }))
    } catch (err) { setError(err.message) }
    finally { setLoading(false) }
  }

  return (
    <div>
      <h2 className="text-xs font-semibold text-slate-500 uppercase tracking-wider mb-3">
        Lead Time Feasibility
      </h2>
      <form onSubmit={run} className="flex gap-2 items-end">
        <div className="w-40">
          <label className="field-label">Lead time (days)</label>
          <input className="field" type="number" min="1" value={leadDays}
            onChange={e => setLeadDays(e.target.value)} placeholder="21" required />
        </div>
        <div className="flex-1">
          <label className="field-label">Required by date</label>
          <input className="field" type="date" value={required}
            min={today} onChange={e => setRequired(e.target.value)} required />
        </div>
        <button type="submit" className="btn-primary" disabled={loading}>
          {loading ? <Spinner size={13} /> : 'Check'}
        </button>
      </form>
      <ErrorBanner message={error} onDismiss={() => setError('')} />
      {result && <RuleResult result={result.result} />}
    </div>
  )
}

// ── Supplier qualification ────────────────────────────────────────────────────

function SupplierQualification() {
  const [supplierId,  setSupplierId]  = useState('')
  const [certs,       setCerts]       = useState('')
  const [minRating,   setMinRating]   = useState('3.5')
  const [result,      setResult]      = useState(null)
  const [error,       setError]       = useState('')
  const [loading,     setLoading]     = useState(false)

  async function run(e) {
    e.preventDefault(); setLoading(true); setError(''); setResult(null)
    try {
      setResult(await api.qualifySupplier({
        supplier_id: supplierId,
        required_certifications: certs.split(',').map(s => s.trim()).filter(Boolean),
        min_rating: Number(minRating),
      }))
    } catch (err) { setError(err.message) }
    finally { setLoading(false) }
  }

  return (
    <div>
      <h2 className="text-xs font-semibold text-slate-500 uppercase tracking-wider mb-3">
        Supplier Qualification
      </h2>
      <form onSubmit={run}>
        <div className="flex gap-2 items-end mb-2">
          <div className="flex-1">
            <IdInput
              label="Supplier ID"
              value={supplierId}
              onChange={setSupplierId}
              entityType="supplier"
              placeholder="Search or enter SUP-001"
            />
          </div>
          <div className="w-28">
            <label className="field-label">Min rating</label>
            <input className="field" type="number" min="0" max="5" step="0.1"
              value={minRating} onChange={e => setMinRating(e.target.value)} />
          </div>
        </div>
        <div className="mb-3">
          <label className="field-label">Required certifications</label>
          <input className="field" value={certs}
            onChange={e => setCerts(e.target.value)}
            placeholder="ISO9001, IATF16949 (comma-separated)" />
        </div>
        <button type="submit" className="btn-primary" disabled={loading}>
          {loading ? <Spinner size={13} /> : 'Qualify'}
        </button>
      </form>
      <ErrorBanner message={error} onDismiss={() => setError('')} />
      {result && <RuleResult result={result.result} />}
    </div>
  )
}

// ── Reasoning page ────────────────────────────────────────────────────────────

export default function Reasoning() {
  return (
    <div className="p-6">
      <div className="flex items-center gap-2 mb-6">
        <Brain size={16} className="text-blue-500" />
        <h1 className="text-sm font-semibold text-slate-700 uppercase tracking-wider">Reasoning Engine</h1>
      </div>

      <div className="space-y-6">
        <div className="panel p-4"><CompatibilityCheck /></div>
        <div className="panel p-4"><LeadTimeCheck /></div>
        <div className="panel p-4"><SupplierQualification /></div>
      </div>
    </div>
  )
}