/**
 * Unified search bar.
 *
 * Routing logic:
 *   - Looks like an ID (starts with a letter, has a dash, e.g. "P-123", "SUP-0")
 *     → hits GET /parts?id_prefix=  or  GET /suppliers?id_prefix=
 *   - Everything else → semantic vector search via GET /search?q=
 *
 * Props:
 *   onResults(results, query, mode)  called when results arrive
 *   placeholder                      input placeholder text
 *   entityType                       'part' | 'supplier' | 'bom' | null (all)
 */

import { useState, useRef } from 'react'
import { Search, X, Hash, Sparkles } from 'lucide-react'
import { request } from '../api/client'

// ID pattern: optional prefix letters, dash, digits/letters
// Matches: P-123, SUP-001, BOM-002, 1568-12779, P-, SUP-
const ID_PATTERN = /^[A-Za-z0-9]+-/

function isIdQuery(q) {
  return ID_PATTERN.test(q.trim())
}

async function runSearch(query, entityType) {
  const q = query.trim()
  if (!q) return { results: [], mode: 'empty' }

  if (isIdQuery(q)) {
    // ID prefix search — hit the list endpoints directly
    const params = new URLSearchParams({ id_prefix: q })
    const types = entityType
      ? [entityType === 'part' ? 'parts' : entityType === 'supplier' ? 'suppliers' : null]
      : ['parts', 'suppliers']

    const results = []
    for (const type of types.filter(Boolean)) {
      try {
        const rows = await request(`/${type}?${params}`)
        rows.forEach(r => results.push({
          entity_id:   r.id,
          entity_type: type === 'parts' ? 'part' : 'supplier',
          name:        r.name,
          score:       null,          // not a similarity score
          data:        r,
        }))
      } catch {}
    }
    return { results, mode: 'id' }
  }

  // Semantic search
  const params = new URLSearchParams({ q, limit: 15, min_score: 0.25 })
  if (entityType) params.set('type', entityType)
  const data = await request(`/search?${params}`)
  return {
    results: data.results.map(r => ({ ...r, score: r.score })),
    mode: 'semantic',
  }
}

export default function SearchBar({
  onResults,
  placeholder = 'Search parts, suppliers, BOMs… or enter an ID like P-123',
  entityType = null,
}) {
  const [query,   setQuery]   = useState('')
  const [loading, setLoading] = useState(false)
  const [mode,    setMode]    = useState(null)   // 'id' | 'semantic' | null
  const timer = useRef(null)

  function handleChange(e) {
    const val = e.target.value
    setQuery(val)

    // Debounce — wait 400ms after typing stops
    clearTimeout(timer.current)
    if (!val.trim()) {
      setMode(null)
      onResults([], '', null)
      return
    }
    timer.current = setTimeout(() => run(val), 400)
  }

  async function run(val) {
    setLoading(true)
    try {
      const { results, mode: m } = await runSearch(val, entityType)
      setMode(m)
      onResults(results, val, m)
    } catch {}
    finally { setLoading(false) }
  }

  function clear() {
    setQuery('')
    setMode(null)
    onResults([], '', null)
  }

  const looksLikeId = isIdQuery(query)

  return (
    <div className="relative">
      <div className="flex items-center gap-2 px-3 py-2 bg-white border border-slate-300
                      rounded focus-within:ring-1 focus-within:ring-slate-400
                      focus-within:border-slate-400">
        {/* Mode icon */}
        {looksLikeId
          ? <Hash size={14} className="text-blue-400 shrink-0" />
          : <Search size={14} className="text-slate-400 shrink-0" />}

        <input
          type="text"
          value={query}
          onChange={handleChange}
          placeholder={placeholder}
          className="flex-1 text-sm outline-none bg-transparent placeholder-slate-400"
        />

        {/* Loading / clear */}
        {loading && (
          <div className="w-3.5 h-3.5 border-2 border-slate-300 border-t-slate-600
                          rounded-full animate-spin shrink-0" />
        )}
        {!loading && query && (
          <button onClick={clear} className="text-slate-300 hover:text-slate-500 shrink-0">
            <X size={13} />
          </button>
        )}
      </div>

      {/* Mode hint */}
      {query && mode && (
        <div className="absolute right-0 mt-1 flex items-center gap-1 text-xs text-slate-400">
          {mode === 'id'
            ? <><Hash size={10} className="text-blue-400" /> ID prefix match</>
            : <><Sparkles size={10} className="text-violet-400" /> Semantic search</>}
        </div>
      )}
    </div>
  )
}