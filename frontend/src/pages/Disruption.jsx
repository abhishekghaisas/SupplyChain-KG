import { useState } from 'react'
import { Zap, AlertTriangle, Search } from 'lucide-react'
import { disruption as api } from '../api/client'
import { SeverityBadge, ErrorBanner, PageSpinner, EmptyState, MonoId, CriticalityBadge } from '../components/ui'
import AIReview from '../components/AIReview'
import SearchBar from '../components/SearchBar'

const ACTION_COLOR = {
  ESCALATE:           'bg-red-100 text-red-700',
  USE_SUBSTITUTE:     'bg-green-100 text-green-700',
  EXPEDITE_ALTERNATE: 'bg-blue-100 text-blue-700',
  DUAL_SOURCE:        'bg-amber-100 text-amber-700',
  MONITOR:            'bg-slate-100 text-slate-600',
}

function ActionBadge({ action }) {
  return (
    <span className={`inline-flex items-center px-2 py-0.5 text-xs font-medium font-mono rounded-sm ${ACTION_COLOR[action] ?? 'bg-slate-100 text-slate-600'}`}>
      {action}
    </span>
  )
}

function ReportView({ report }) {
  if (!report) return null

  return (
    <div className="mt-6">
      {/* Summary header */}
      <div className="panel mb-4">
        <div className="px-4 py-3 flex items-start justify-between">
          <div>
            <p className="text-sm font-semibold text-slate-800">{report.disrupted_name}</p>
            <p className="text-xs text-slate-500 font-mono mt-0.5">{report.disrupted_id}</p>
          </div>
          <div className="text-right">
            <p className="text-xs text-slate-400">Scenario: <span className="font-medium text-slate-600">{report.scenario}</span></p>
            <p className="text-xs text-slate-400 mt-0.5">Scope: {report.bom_statuses.join(', ')}</p>
          </div>
        </div>
        <div className="px-4 py-3 border-t border-slate-100 bg-slate-50 text-sm text-slate-600">
          {report.summary}
        </div>
      </div>

      {report.affected_boms.length === 0 ? (
        <EmptyState message="No BOMs affected in the selected scope" />
      ) : (
        <div className="space-y-3">
          {report.affected_boms
            .sort((a, b) => b.severity_score - a.severity_score)
            .map(bom => (
              <div key={bom.bom_id} className="panel">
                {/* BOM header */}
                <div className="panel-header">
                  <div className="flex items-center gap-3">
                    <SeverityBadge label={bom.severity_label} />
                    <span className="panel-title">{bom.bom_name}</span>
                    <MonoId value={bom.bom_id} />
                    <span className="font-mono text-xs text-slate-400">v{bom.bom_version}</span>
                  </div>
                  <div className="flex items-center gap-3">
                    <div className="text-xs text-slate-400">
                      Severity: <span className="font-mono font-medium text-slate-600">{bom.severity_score.toFixed(2)}</span>
                    </div>
                  </div>
                </div>

                <div className="px-4 py-3">
                  {/* Recommended actions */}
                  <div className="flex flex-wrap gap-1.5 mb-3">
                    {bom.actions.map(a => <ActionBadge key={a} action={a} />)}
                  </div>

                  {/* Disrupted parts */}
                  <table className="data-table">
                    <thead>
                      <tr>
                        <th>Part</th>
                        <th>Criticality</th>
                        <th>Qty in BOM</th>
                        <th>Alternate suppliers</th>
                        <th>Substitute available</th>
                      </tr>
                    </thead>
                    <tbody>
                      {bom.disrupted_parts.map(dp => (
                        <tr key={dp.part_id}>
                          <td>
                            <MonoId value={dp.part_id} />
                            <div className="text-xs text-slate-600 mt-0.5">{dp.part_name}</div>
                          </td>
                          <td><CriticalityBadge value={dp.criticality} /></td>
                          <td className="text-sm">{dp.quantity_in_bom}</td>
                          <td>
                            <span className={`text-sm font-medium ${dp.alternate_supplier_count === 0 ? 'text-red-600' : dp.alternate_supplier_count === 1 ? 'text-amber-600' : 'text-green-600'}`}>
                              {dp.alternate_supplier_count}
                            </span>
                          </td>
                          <td>
                            {dp.has_substitute ? (
                              <div>
                                <span className="text-xs text-green-600 font-medium">✓ Yes</span>
                                {dp.substitutes.slice(0, 1).map(s => (
                                  <div key={s.part_id} className="text-xs text-slate-400 font-mono mt-0.5">
                                    → {s.part_id}
                                  </div>
                                ))}
                              </div>
                            ) : (
                              <span className="text-xs text-slate-400">None on record</span>
                            )}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </div>
            ))}
        </div>
      )}
    </div>
  )
}

export default function Disruption() {
  const [mode,     setMode]     = useState('supplier')  // 'supplier' | 'part'
  const [id,       setId]       = useState('')
  const [statuses, setStatuses] = useState(['RELEASED'])
  const [searchResults, setSearchResults] = useState([])
  const [showDropdown,  setShowDropdown]  = useState(false)
  const [report,   setReport]   = useState(null)
  const [loading,  setLoading]  = useState(false)
  const [error,    setError]    = useState('')

  const STATUS_OPTIONS = ['DRAFT', 'REVIEW', 'RELEASED', 'ARCHIVED']

  function toggleStatus(s) {
    setStatuses(prev => prev.includes(s) ? prev.filter(x => x !== s) : [...prev, s])
  }

  async function handleAnalyse() {
    if (!id.trim()) return
    setLoading(true); setError(''); setReport(null)
    try {
      const fn   = mode === 'supplier' ? api.supplier : api.part
      const data = await fn(id.trim(), statuses.join(','))
      setReport(data)
    } catch (err) {
      setError(err.message)
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="p-6">
      <div className="flex items-center gap-2 mb-6">
        <Zap size={16} className="text-amber-500" />
        <h1 className="text-sm font-semibold text-slate-700 uppercase tracking-wider">Disruption Analysis</h1>
      </div>

      {/* Controls */}
      <div className="panel mb-6">
        <div className="p-4 space-y-4">
          {/* Mode toggle */}
          <div className="flex gap-1 bg-slate-100 p-1 rounded w-fit">
            {[['supplier', 'Supplier disruption'], ['part', 'Part disruption']].map(([m, label]) => (
              <button key={m} onClick={() => { setMode(m); setReport(null); setId('') }}
                className={`px-3 py-1.5 text-xs font-medium rounded transition-colors
                  ${mode === m ? 'bg-white text-slate-800 shadow-sm' : 'text-slate-500 hover:text-slate-700'}`}>
                {label}
              </button>
            ))}
          </div>

          {/* ID input with search */}
          <div className="flex gap-2">
            <div className="flex-1">
              <label className="field-label">
                {mode === 'supplier' ? 'Supplier ID' : 'Part ID'}
              </label>
              <SearchBar
                entityType={mode === 'supplier' ? 'supplier' : 'part'}
                placeholder={mode === 'supplier'
                  ? 'Search by name, location… or ID like SUP-001'
                  : 'Search by name, specs… or ID like P-12345'}
                onResults={(results, q) => {
                  setSearchResults(results)
                  setShowDropdown(results.length > 0 && q.length > 0)
                }}
              />
              {/* Search result dropdown */}
              {showDropdown && searchResults.length > 0 && (
                <div className="relative">
                  <div className="absolute z-20 top-1 left-0 right-0 bg-white border
                                  border-slate-200 rounded shadow-lg max-h-48 overflow-y-auto">
                    {searchResults.map(r => (
                      <button key={r.entity_id}
                        onClick={() => { setId(r.entity_id); setShowDropdown(false) }}
                        className="w-full flex items-center gap-2 px-3 py-2 text-left
                                   hover:bg-slate-50 border-b border-slate-100 last:border-0">
                        <span className="font-mono text-xs text-slate-400 w-28 shrink-0 truncate">
                          {r.entity_id}
                        </span>
                        <span className="text-sm text-slate-700 truncate">{r.name}</span>
                        {r.score && (
                          <span className="font-mono text-xs text-slate-300 ml-auto shrink-0">
                            {r.score.toFixed(2)}
                          </span>
                        )}
                      </button>
                    ))}
                  </div>
                </div>
              )}
              {/* Selected ID display */}
              {id && (
                <div className="mt-1.5 flex items-center gap-2">
                  <span className="text-xs text-slate-400">Selected:</span>
                  <span className="font-mono text-xs bg-slate-100 text-slate-700 px-2 py-0.5 rounded">
                    {id}
                  </span>
                  <button onClick={() => setId('')}
                    className="text-xs text-slate-400 hover:text-slate-600">×</button>
                  <button className="btn-primary ml-auto" onClick={handleAnalyse}
                    disabled={loading}>
                    {loading ? 'Analysing…' : <><Search size={13} /> Analyse</>}
                  </button>
                </div>
              )}
              {!id && (
                <div className="mt-1.5 flex justify-end">
                  <button className="btn-primary" onClick={handleAnalyse}
                    disabled={loading || !id.trim()}>
                    {loading ? 'Analysing…' : <><Search size={13} /> Analyse</>}
                  </button>
                </div>
              )}
            </div>
          </div>

          {/* BOM status scope */}
          <div>
            <label className="field-label">BOM scope</label>
            <div className="flex gap-2 flex-wrap">
              {STATUS_OPTIONS.map(s => (
                <button key={s} onClick={() => toggleStatus(s)}
                  className={`px-2.5 py-1 text-xs font-medium rounded border transition-colors
                    ${statuses.includes(s)
                      ? 'bg-slate-800 text-white border-slate-800'
                      : 'bg-white text-slate-500 border-slate-300 hover:border-slate-400'}`}>
                  {s}
                </button>
              ))}
            </div>
          </div>
        </div>
      </div>

      <ErrorBanner message={error} onDismiss={() => setError('')} />

      {loading && <PageSpinner />}
      {report  && (
        <>
          <ReportView report={report} />
          <div className="mt-4">
            <AIReview
              title="Executive Summary"
              buttonLabel="Narrate with AI"
              onGenerate={() => api.aiNarrate({
                disrupted_id:   report.disrupted_id,
                disrupted_type: report.scenario,
                report:         report,
              })}
            />
          </div>
        </>
      )}
      {!loading && !report && !error && (
        <div className="flex flex-col items-center justify-center py-20 text-slate-300">
          <AlertTriangle size={32} className="mb-3" />
          <p className="text-sm">Enter an ID above and click Analyse to model a disruption scenario</p>
        </div>
      )}
    </div>
  )
}