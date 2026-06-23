/**
 * AIReview — renders a grounded Claude response with audit trail.
 *
 * Used by BOMs (review), Suppliers (qualify), and Disruption (narrate).
 * Shows the markdown content, token usage, model used, and the exact
 * graph context Claude was given — so every AI output is auditable.
 */

import { useState } from 'react'
import { Sparkles, ChevronDown, ChevronRight, Database, Zap } from 'lucide-react'
import { Spinner } from './ui'

// ── Simple markdown renderer ──────────────────────────────────────────────────
// Handles ## headers, **bold**, bullet points, and line breaks.
// No external dependency needed for this level of markdown.

function renderMarkdown(text) {
  if (!text) return null
  const lines = text.split('\n')
  const elements = []
  let key = 0

  for (let i = 0; i < lines.length; i++) {
    const line = lines[i]

    if (line.startsWith('## ')) {
      elements.push(
        <h2 key={key++} className="text-sm font-semibold text-slate-800 mt-5 mb-2 first:mt-0">
          {line.slice(3)}
        </h2>
      )
    } else if (line.startsWith('### ')) {
      elements.push(
        <h3 key={key++} className="text-xs font-semibold text-slate-600 uppercase tracking-wider mt-4 mb-1.5">
          {line.slice(4)}
        </h3>
      )
    } else if (line.startsWith('**') && line.endsWith('**') && line.length > 4) {
      elements.push(
        <p key={key++} className="text-sm font-semibold text-slate-800 mb-2">
          {line.slice(2, -2)}
        </p>
      )
    } else if (line.match(/^\*\*[^*]+:\*\*/)) {
      // **Label:** value
      const match = line.match(/^\*\*([^*]+):\*\*(.*)/)
      if (match) {
        elements.push(
          <p key={key++} className="text-sm mb-1">
            <span className="font-semibold text-slate-700">{match[1]}:</span>
            <span className="text-slate-600">{match[2]}</span>
          </p>
        )
      }
    } else if (line.startsWith('- ')) {
      elements.push(
        <div key={key++} className="flex gap-2 mb-1">
          <span className="text-slate-300 mt-0.5 shrink-0">•</span>
          <span className="text-sm text-slate-700">{inlineBold(line.slice(2))}</span>
        </div>
      )
    } else if (line.trim() === '') {
      elements.push(<div key={key++} className="h-1" />)
    } else {
      elements.push(
        <p key={key++} className="text-sm text-slate-700 mb-1">
          {inlineBold(line)}
        </p>
      )
    }
  }
  return elements
}

function inlineBold(text) {
  const parts = text.split(/(\*\*[^*]+\*\*)/)
  return parts.map((part, i) =>
    part.startsWith('**') && part.endsWith('**')
      ? <strong key={i} className="font-semibold text-slate-800">{part.slice(2, -2)}</strong>
      : part
  )
}

// ── Audit trail panel ─────────────────────────────────────────────────────────

function AuditTrail({ response }) {
  const [open, setOpen] = useState(false)
  const { token_usage, model, context } = response

  return (
    <div className="mt-4 border-t border-slate-100 pt-3">
      <button
        onClick={() => setOpen(v => !v)}
        className="flex items-center gap-2 text-xs text-slate-400 hover:text-slate-600 transition-colors"
      >
        {open ? <ChevronDown size={12} /> : <ChevronRight size={12} />}
        <Database size={11} />
        <span>Grounding audit trail</span>
        <span className="ml-2 font-mono">
          {token_usage.total} tokens · {model}
        </span>
      </button>

      {open && (
        <div className="mt-3 space-y-3">
          {/* Token usage */}
          <div className="flex gap-4 text-xs font-mono text-slate-500">
            <span>prompt: {token_usage.prompt}</span>
            <span>output: {token_usage.output}</span>
            <span>total: {token_usage.total}</span>
          </div>

          {/* Data sources */}
          <div>
            <p className="text-xs font-medium text-slate-400 mb-1">Graph queries run:</p>
            <div className="flex flex-wrap gap-1">
              {(context.data_sources || []).map(s => (
                <span key={s} className="font-mono text-xs bg-slate-100 text-slate-500 px-1.5 py-0.5 rounded">
                  {s}
                </span>
              ))}
            </div>
          </div>

          {/* Context preview */}
          <div>
            <p className="text-xs font-medium text-slate-400 mb-1">
              Context injected into prompt ({context.subject} · fetched {context.fetched_at?.slice(0, 19)}):
            </p>
            <pre className="text-xs font-mono text-slate-500 bg-slate-50 border border-slate-200
                            rounded p-3 overflow-x-auto max-h-48 whitespace-pre-wrap">
              {JSON.stringify(context.data, null, 2).slice(0, 2000)}
              {JSON.stringify(context.data, null, 2).length > 2000 ? '\n… (truncated)' : ''}
            </pre>
          </div>
        </div>
      )}
    </div>
  )
}

// ── Main component ────────────────────────────────────────────────────────────

export default function AIReview({
  title,
  onGenerate,       // async fn() → response dict
  buttonLabel = 'Generate AI Analysis',
}) {
  const [response, setResponse] = useState(null)
  const [loading,  setLoading]  = useState(false)
  const [error,    setError]    = useState('')

  async function generate() {
    setLoading(true); setError('')
    try {
      const r = await onGenerate()
      setResponse(r)
    } catch (err) {
      setError(err.message)
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="mt-4">
      {!response && !loading && (
        <button
          onClick={generate}
          className="flex items-center gap-2 px-3 py-2 text-sm font-medium
                     bg-violet-50 text-violet-700 border border-violet-200 rounded
                     hover:bg-violet-100 transition-colors"
        >
          <Sparkles size={14} />
          {buttonLabel}
        </button>
      )}

      {loading && (
        <div className="flex items-center gap-2 px-3 py-2 text-sm text-slate-500
                        bg-slate-50 border border-slate-200 rounded">
          <Spinner size={14} />
          <span>Claude is analysing graph data…</span>
        </div>
      )}

      {error && (
        <div className="px-3 py-2 text-sm text-red-600 bg-red-50 border border-red-200 rounded">
          {error}
        </div>
      )}

      {response && (
        <div className="panel">
          {/* Header */}
          <div className="panel-header">
            <div className="flex items-center gap-2">
              <Sparkles size={13} className="text-violet-500" />
              <span className="panel-title">{title}</span>
            </div>
            <button
              onClick={() => setResponse(null)}
              className="text-xs text-slate-400 hover:text-slate-600"
            >
              Regenerate
            </button>
          </div>

          {/* Content */}
          <div className="px-4 py-4">
            {renderMarkdown(response.content)}
            <AuditTrail response={response} />
          </div>
        </div>
      )}
    </div>
  )
}