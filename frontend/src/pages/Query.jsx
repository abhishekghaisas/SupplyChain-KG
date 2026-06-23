import { useState } from 'react'
import { MessageSquare, ChevronDown, ChevronRight, Database, Sparkles } from 'lucide-react'
import { request } from '../api/client'
import { ErrorBanner, Spinner } from '../components/ui'

const EXAMPLE_QUESTIONS = [
  "Which parts have only one active supplier?",
  "Show me all CRITICAL parts without a verified substitute",
  "Which suppliers have a quality rating above 4.5?",
  "What BOMs contain parts from German suppliers?",
  "Which parts have a lead time over 30 days?",
  "Show me all electronic parts with HIGH or CRITICAL criticality",
  "Which suppliers are single-source for any part?",
  "What parts have verified substitutes available?",
]

function CypherBlock({ cypher }) {
  const [open, setOpen] = useState(false)
  return (
    <div className="mt-3">
      <button onClick={() => setOpen(v => !v)}
        className="flex items-center gap-1.5 text-xs text-slate-400 hover:text-slate-600">
        {open ? <ChevronDown size={11} /> : <ChevronRight size={11} />}
        <Database size={11} />
        View generated Cypher
      </button>
      {open && (
        <pre className="mt-2 text-xs font-mono text-slate-600 bg-slate-50 border border-slate-200
                        rounded p-3 overflow-x-auto whitespace-pre-wrap">
          {cypher}
        </pre>
      )}
    </div>
  )
}

function RawResults({ results, highlightedRow, onRowClick }) {
  const [open, setOpen] = useState(true)
  if (!results?.length) return null
  return (
    <div className="mt-3">
      <button onClick={() => setOpen(v => !v)}
        className="flex items-center gap-1.5 text-xs text-slate-400 hover:text-slate-600">
        {open ? <ChevronDown size={11} /> : <ChevronRight size={11} />}
        <span>Source data ({results.length} rows)</span>
      </button>
      {open && (
        <div className="mt-2 overflow-x-auto">
          <table className="data-table text-xs">
            <thead>
              <tr>
                <th className="w-8">#</th>
                {Object.keys(results[0]).map(k => <th key={k}>{k}</th>)}
              </tr>
            </thead>
            <tbody>
              {results.slice(0, 20).map((row, i) => (
                <tr key={i}
                  onClick={() => onRowClick(i + 1)}
                  className={`cursor-pointer transition-colors ${
                    highlightedRow === i + 1
                      ? 'bg-violet-50 ring-1 ring-inset ring-violet-200'
                      : 'hover:bg-slate-50'
                  }`}>
                  <td className="font-mono text-slate-300 text-center">{i + 1}</td>
                  {Object.values(row).map((v, j) => (
                    <td key={j} className="font-mono text-slate-600">
                      {v === null ? <span className="text-slate-300">—</span> :
                       typeof v === 'object' ? JSON.stringify(v) : String(v)}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
          {results.length > 20 && (
            <p className="text-xs text-slate-400 mt-1">Showing 20 of {results.length} rows</p>
          )}
        </div>
      )}
    </div>
  )
}

function ResultCard({ result }) {
  const [highlightedRow, setHighlightedRow] = useState(null)

  // Parse [row N] and [row N, M] citations into clickable spans
  function renderWithCitations(text, onCiteClick) {
    const parts = text.split(/(\[rows? [\d,\s\-]+\]|\[no data found\])/g)
    return parts.map((part, i) => {
      const rowMatch = part.match(/\[rows? ([\d,\s\-]+)\]/)
      if (rowMatch) {
        // Parse first row number from citation for highlighting
        const firstRow = parseInt(rowMatch[1].split(/[\s,\-]/)[0])
        return (
          <button key={i} onClick={() => onCiteClick(firstRow)}
            className="inline-flex items-center px-1 py-0.5 mx-0.5 text-xs font-mono
                       bg-violet-100 text-violet-700 rounded hover:bg-violet-200
                       transition-colors cursor-pointer">
            {part}
          </button>
        )
      }
      if (part === '[no data found]') {
        return (
          <span key={i} className="inline px-1 py-0.5 mx-0.5 text-xs font-mono
                                    bg-slate-100 text-slate-500 rounded">
            {part}
          </span>
        )
      }
      // Render **bold** inline
      const boldParts = part.split(/(\*\*[^*]+\*\*)/g)
      return boldParts.map((bp, j) =>
        bp.startsWith('**') && bp.endsWith('**')
          ? <strong key={`${i}-${j}`} className="font-semibold text-slate-800">{bp.slice(2,-2)}</strong>
          : bp
      )
    })
  }

  function renderAnswer(text, onCiteClick) {
    return text.split('\n').map((line, i) => {
      if (line.startsWith('- ') || line.startsWith('• ')) {
        return (
          <div key={i} className="flex gap-2 mb-1">
            <span className="text-slate-300 shrink-0 mt-0.5">•</span>
            <span className="text-sm text-slate-700 leading-relaxed">
              {renderWithCitations(line.slice(2), onCiteClick)}
            </span>
          </div>
        )
      }
      if (!line.trim()) return <div key={i} className="h-1" />
      return (
        <p key={i} className="text-sm text-slate-700 mb-1 leading-relaxed">
          {renderWithCitations(line, onCiteClick)}
        </p>
      )
    })
  }

  return (
    <div className="panel mt-4">
      <div className="panel-header">
        <div className="flex items-center gap-2">
          <Sparkles size={13} className="text-violet-500" />
          <span className="panel-title">Answer</span>
          <span className="text-xs text-slate-400 font-mono ml-2">
            {result.row_count} rows · {result.token_usage.total} tokens · {result.model}
          </span>
        </div>
        {highlightedRow && (
          <span className="text-xs text-violet-600 font-mono">
            row {highlightedRow} highlighted ↓
          </span>
        )}
      </div>
      <div className="px-4 py-4">
        <div className="mb-4">
          {renderAnswer(result.answer, setHighlightedRow)}
        </div>
        {highlightedRow && (
          <p className="text-xs text-slate-400 mb-2">
            Click a citation to highlight its row · click the row to dismiss
          </p>
        )}
        <CypherBlock cypher={result.cypher} />
        <RawResults
          results={result.raw_results}
          highlightedRow={highlightedRow}
          onRowClick={n => setHighlightedRow(h => h === n ? null : n)}
        />
      </div>
    </div>
  )
}

export default function Query() {
  const [question, setQuestion] = useState('')
  const [result,   setResult]   = useState(null)
  const [loading,  setLoading]  = useState(false)
  const [error,    setError]    = useState('')
  const [history,  setHistory]  = useState([])

  async function runQuery(q) {
    const question = q || ''
    if (!question.trim()) return
    setLoading(true); setError(''); setResult(null)
    try {
      const r = await request('/query', {
        method: 'POST',
        body: JSON.stringify({ question }),
      })
      setResult(r)
      setHistory(h => [{ question, result: r }, ...h.slice(0, 4)])
    } catch (err) {
      setError(err.message)
    } finally {
      setLoading(false)
    }
  }

  function handleSubmit(e) {
    e.preventDefault()
    runQuery(question)
  }

  return (
    <div className="p-6 max-w-3xl">
      {/* Header */}
      <div className="flex items-center gap-2 mb-2">
        <MessageSquare size={16} className="text-violet-500" />
        <h1 className="text-sm font-semibold text-slate-700 uppercase tracking-wider">
          Ask the Graph
        </h1>
      </div>
      <p className="text-sm text-slate-500 mb-6">
        Ask questions in plain English. Claude converts them to Cypher, queries the graph,
        and interprets the results — grounded in your actual data.
      </p>

      {/* Input */}
      <form onSubmit={handleSubmit} className="panel p-4 mb-4">
        <div className="flex gap-2">
          <input
            className="field flex-1 text-sm"
            value={question}
            onChange={e => setQuestion(e.target.value)}
            placeholder="Which parts have only one active supplier?"
            disabled={loading}
            autoFocus
          />
          <button type="submit" className="btn-primary" disabled={loading || !question.trim()}>
            {loading ? <Spinner size={13} /> : 'Ask'}
          </button>
        </div>

        {/* Example questions */}
        <div className="mt-3">
          <p className="text-xs text-slate-400 mb-2">Try these:</p>
          <div className="flex flex-wrap gap-1.5">
            {EXAMPLE_QUESTIONS.map(q => (
              <button
                key={q}
                type="button"
                onClick={() => { setQuestion(q); runQuery(q) }}
                className="text-xs px-2 py-1 bg-slate-50 border border-slate-200
                           text-slate-600 rounded hover:bg-slate-100 transition-colors text-left"
              >
                {q}
              </button>
            ))}
          </div>
        </div>
      </form>

      <ErrorBanner message={error} onDismiss={() => setError('')} />

      {loading && (
        <div className="flex items-center gap-2 px-4 py-3 text-sm text-slate-500
                        bg-slate-50 border border-slate-200 rounded">
          <Spinner size={14} />
          <span>Claude is generating and running the query…</span>
        </div>
      )}

      {result && <ResultCard result={result} />}

      {/* Query history */}
      {history.length > 1 && (
        <div className="mt-8">
          <p className="text-xs font-medium text-slate-400 uppercase tracking-wider mb-3">
            Recent queries
          </p>
          <div className="space-y-2">
            {history.slice(1).map((h, i) => (
              <button key={i} onClick={() => { setQuestion(h.question); setResult(h.result) }}
                className="w-full text-left px-3 py-2 text-sm text-slate-600
                           bg-white border border-slate-200 rounded hover:bg-slate-50
                           transition-colors truncate">
                {h.question}
              </button>
            ))}
          </div>
        </div>
      )}
    </div>
  )
}