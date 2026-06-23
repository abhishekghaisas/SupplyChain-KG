/**
 * SubstituteSuggestions — review AI-inferred substitute candidates.
 *
 * Shows each candidate with:
 *   - Confidence score and verdict badge
 *   - One-sentence summary of why it's compatible
 *   - Per-spec comparison table (match/mismatch with notes)
 *   - Full Claude reasoning
 *   - Checkbox to select for persisting to the graph
 *
 * Usage:
 *   <SubstituteSuggestions partId="P-12345" />
 */

import { useState } from 'react'
import { Sparkles, CheckCircle, XCircle, AlertCircle, ChevronDown, ChevronRight, Save } from 'lucide-react'
import { parts as api } from '../api/client'
import { Spinner, ErrorBanner } from './ui'

// ── Verdict badge ─────────────────────────────────────────────────────────────

function VerdictBadge({ verdict, confidence }) {
  const config = {
    COMPATIBLE:        { color: 'bg-green-100 text-green-700',  icon: CheckCircle,   label: 'Compatible' },
    LIKELY_COMPATIBLE: { color: 'bg-amber-100 text-amber-700',  icon: AlertCircle,   label: 'Likely compatible' },
    INCOMPATIBLE:      { color: 'bg-red-100 text-red-700',      icon: XCircle,       label: 'Incompatible' },
  }
  const { color, icon: Icon, label } = config[verdict] ?? config.LIKELY_COMPATIBLE

  return (
    <div className={`inline-flex items-center gap-1.5 px-2 py-1 rounded text-xs font-medium ${color}`}>
      <Icon size={12} />
      <span>{label}</span>
      <span className="font-mono opacity-70">({(confidence * 100).toFixed(0)}%)</span>
    </div>
  )
}

// ── Spec comparison table ─────────────────────────────────────────────────────

function SpecTable({ comparisons }) {
  if (!comparisons?.length) return null
  return (
    <table className="w-full text-xs border-collapse mt-2">
      <thead>
        <tr className="border-b border-slate-100">
          <th className="text-left py-1.5 px-2 text-slate-400 font-medium w-32">Spec</th>
          <th className="text-left py-1.5 px-2 text-slate-400 font-medium">Source</th>
          <th className="text-left py-1.5 px-2 text-slate-400 font-medium">Candidate</th>
          <th className="text-left py-1.5 px-2 text-slate-400 font-medium w-8"></th>
          <th className="text-left py-1.5 px-2 text-slate-400 font-medium">Note</th>
        </tr>
      </thead>
      <tbody>
        {comparisons.map((sc, i) => (
          <tr key={i} className={`border-b border-slate-50 ${
            !sc.match && sc.material ? 'bg-red-50' :
            !sc.match ? 'bg-amber-50' : ''
          }`}>
            <td className="py-1.5 px-2 font-mono text-slate-600">{sc.spec}</td>
            <td className="py-1.5 px-2 text-slate-700">{String(sc.source ?? '—')}</td>
            <td className="py-1.5 px-2 text-slate-700">{String(sc.candidate ?? '—')}</td>
            <td className="py-1.5 px-2 text-center">
              {sc.match
                ? <span className="text-green-500">✓</span>
                : sc.material
                  ? <span className="text-red-500">✗</span>
                  : <span className="text-amber-500">~</span>}
            </td>
            <td className="py-1.5 px-2 text-slate-500">{sc.note}</td>
          </tr>
        ))}
      </tbody>
    </table>
  )
}

// ── Single suggestion card ────────────────────────────────────────────────────

function SuggestionCard({ suggestion: s, selected, onToggle }) {
  const [expanded, setExpanded] = useState(false)

  return (
    <div className={`border rounded transition-colors ${
      selected ? 'border-violet-300 bg-violet-50' : 'border-slate-200 bg-white'
    }`}>
      {/* Header */}
      <div className="flex items-start gap-3 p-3">
        <input
          type="checkbox"
          checked={selected}
          onChange={onToggle}
          className="mt-0.5 h-4 w-4 accent-violet-600 cursor-pointer shrink-0"
          disabled={s.verdict === 'INCOMPATIBLE'}
        />
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2 flex-wrap mb-1">
            <span className="font-mono text-xs text-slate-500">{s.candidate_part_id}</span>
            <span className="text-sm font-medium text-slate-800 truncate">{s.candidate_part_name}</span>
            <VerdictBadge verdict={s.verdict} confidence={s.confidence} />
          </div>
          <p className="text-xs text-slate-600">{s.summary}</p>
          {s.matching_specs?.length > 0 && (
            <div className="flex flex-wrap gap-1 mt-1.5">
              <span className="text-xs text-slate-400">Matches:</span>
              {s.matching_specs.map(spec => (
                <span key={spec} className="text-xs bg-green-50 text-green-700 px-1.5 py-0.5 rounded font-mono">
                  {spec}
                </span>
              ))}
            </div>
          )}
          {s.differing_specs?.length > 0 && (
            <div className="flex flex-wrap gap-1 mt-1">
              <span className="text-xs text-slate-400">Differs:</span>
              {s.differing_specs.map(spec => (
                <span key={spec} className="text-xs bg-red-50 text-red-700 px-1.5 py-0.5 rounded font-mono">
                  {spec}
                </span>
              ))}
            </div>
          )}
        </div>
        <div className="text-right shrink-0">
          <div className="text-xs text-slate-400">Semantic</div>
          <div className="font-mono text-xs text-slate-600">{s.semantic_score?.toFixed(2)}</div>
        </div>
      </div>

      {/* Expandable detail */}
      <div className="border-t border-slate-100">
        <button
          onClick={() => setExpanded(v => !v)}
          className="flex items-center gap-1.5 px-3 py-1.5 text-xs text-slate-400 hover:text-slate-600 w-full"
        >
          {expanded ? <ChevronDown size={11} /> : <ChevronRight size={11} />}
          Spec comparison & reasoning
        </button>
        {expanded && (
          <div className="px-3 pb-3">
            <SpecTable comparisons={s.spec_comparisons} />
            {s.reasoning && (
              <p className="mt-3 text-xs text-slate-600 italic border-l-2 border-slate-200 pl-3">
                {s.reasoning}
              </p>
            )}
          </div>
        )}
      </div>
    </div>
  )
}

// ── Main component ────────────────────────────────────────────────────────────

export default function SubstituteSuggestions({ partId }) {
  const [suggestions, setSuggestions]   = useState(null)
  const [selected,    setSelected]      = useState(new Set())
  const [loading,     setLoading]       = useState(false)
  const [persisting,  setPersisting]    = useState(false)
  const [persisted,   setPersisted]     = useState(false)
  const [error,       setError]         = useState('')

  async function analyse() {
    setLoading(true); setError(''); setSuggestions(null)
    setPersisted(false); setSelected(new Set())
    try {
      const r = await api.suggestSubstitutes(partId, 5)
      setSuggestions(r.suggestions || [])
      // Pre-select all COMPATIBLE suggestions
      const autoSelect = new Set(
        (r.suggestions || [])
          .filter(s => s.verdict === 'COMPATIBLE')
          .map(s => s.candidate_part_id)
      )
      setSelected(autoSelect)
    } catch (err) {
      setError(err.message)
    } finally {
      setLoading(false)
    }
  }

  function toggle(id) {
    setSelected(prev => {
      const next = new Set(prev)
      next.has(id) ? next.delete(id) : next.add(id)
      return next
    })
  }

  async function persist() {
    const toSave = (suggestions || []).filter(s => selected.has(s.candidate_part_id))
    if (!toSave.length) return
    setPersisting(true); setError('')
    try {
      const r = await api.persistSubstitutes(partId, {
        suggestions: toSave,
        min_confidence: 0.5,
      })
      setPersisted(true)
      setError('')
    } catch (err) {
      setError(err.message)
    } finally {
      setPersisting(false)
    }
  }

  if (!suggestions && !loading) {
    return (
      <button
        onClick={analyse}
        className="flex items-center gap-2 px-3 py-2 text-sm font-medium
                   bg-violet-50 text-violet-700 border border-violet-200 rounded
                   hover:bg-violet-100 transition-colors"
      >
        <Sparkles size={14} />
        Find substitute candidates with AI
      </button>
    )
  }

  if (loading) {
    return (
      <div className="flex items-center gap-2 px-3 py-3 text-sm text-slate-500
                      bg-slate-50 border border-slate-200 rounded">
        <Spinner size={14} />
        <span>Claude is comparing specifications…</span>
      </div>
    )
  }

  return (
    <div>
      <div className="flex items-center justify-between mb-3">
        <div className="flex items-center gap-2">
          <Sparkles size={13} className="text-violet-500" />
          <span className="text-sm font-medium text-slate-700">
            {suggestions.length === 0
              ? 'No compatible candidates found'
              : `${suggestions.length} candidate${suggestions.length > 1 ? 's' : ''} found`}
          </span>
        </div>
        <button onClick={analyse} className="text-xs text-slate-400 hover:text-slate-600">
          Re-analyse
        </button>
      </div>

      <ErrorBanner message={error} onDismiss={() => setError('')} />

      {suggestions.length === 0 ? (
        <p className="text-sm text-slate-500">
          No parts in the same category with sufficient similarity were found.
          This could mean the part is unique in your catalog, or the catalog
          needs more parts before substitutes can be inferred.
        </p>
      ) : (
        <>
          <p className="text-xs text-slate-400 mb-3">
            Check the boxes next to candidates you want to add as inferred substitutes.
            They'll appear in BOM reviews as "inferred — requires validation."
          </p>
          <div className="space-y-2 mb-4">
            {suggestions.map(s => (
              <SuggestionCard
                key={s.candidate_part_id}
                suggestion={s}
                selected={selected.has(s.candidate_part_id)}
                onToggle={() => toggle(s.candidate_part_id)}
              />
            ))}
          </div>

          <div className="flex items-center justify-between pt-3 border-t border-slate-200">
            <span className="text-xs text-slate-400">
              {selected.size} selected · will be saved as INFERRED (requires engineer validation)
            </span>
            {persisted ? (
              <div className="flex items-center gap-1.5 text-sm text-green-600">
                <CheckCircle size={14} />
                Saved to graph
              </div>
            ) : (
              <button
                onClick={persist}
                disabled={persisting || selected.size === 0}
                className="flex items-center gap-1.5 px-3 py-1.5 text-sm font-medium
                           bg-slate-900 text-white rounded hover:bg-slate-700
                           disabled:opacity-40 transition-colors"
              >
                {persisting ? <Spinner size={13} /> : <Save size={13} />}
                Save {selected.size > 0 ? `${selected.size} ` : ''}to graph
              </button>
            )}
          </div>
        </>
      )}
    </div>
  )
}