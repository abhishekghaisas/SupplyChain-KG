import { useState, useRef } from 'react'
import { Sparkles, Upload, FileText, CheckCircle, XCircle, ChevronDown, ChevronRight } from 'lucide-react'
import { request } from '../api/client'
import { CriticalityBadge, ErrorBanner, Spinner, MonoId } from '../components/ui'

// ── API call (not in client.js yet — inline here) ─────────────────────────────

async function extractEntities({ text, document_type, source, persist }) {
  return request('/extraction/extract', {
    method: 'POST',
    body: JSON.stringify({ text, document_type, source, persist }),
  })
}

// ── Sample document for demo ──────────────────────────────────────────────────

const SAMPLE_TEXT = `SUPPLIER CATALOG — Nordic Hydraulics AB
Location: Sweden | Certifications: ISO9001, ISO14001, CE
Contact: sales@nordichydraulics.se | +46-8-123-4567

PRODUCT LISTING
───────────────

Part No: NH-HP-300
Name: Hydraulic Pump HP-300
Category: Mechanical
Description: High-pressure gear pump for industrial automation
Flow Rate: 300 L/min | Max Pressure: 350 bar | Input Voltage: 24V DC
Weight: 4.2 kg | Certifications: CE, ISO9001
Unit Price: $2,150.00 | Lead Time: 21 days | MOQ: 5

Part No: NH-HV-100  
Name: Hydraulic Valve HV-100
Category: Mechanical
Description: Proportional control valve, electronically actuated
Max Flow: 100 L/min | Pressure Rating: 400 bar
Certifications: CE, RoHS
Unit Price: $485.00 | Lead Time: 14 days | MOQ: 10`

// ── Confidence meter ──────────────────────────────────────────────────────────

function ConfidenceMeter({ value }) {
  const pct   = Math.round(value * 100)
  const color = value >= 0.85 ? 'bg-green-500' : value >= 0.6 ? 'bg-amber-500' : 'bg-red-500'
  return (
    <div className="flex items-center gap-2">
      <div className="flex-1 bg-slate-100 rounded-full h-1.5">
        <div className={`${color} h-1.5 rounded-full transition-all`} style={{ width: `${pct}%` }} />
      </div>
      <span className="font-mono text-xs text-slate-600 w-8 text-right">{pct}%</span>
    </div>
  )
}

// ── Collapsible section ───────────────────────────────────────────────────────

function Section({ title, count, badge, children, defaultOpen = true }) {
  const [open, setOpen] = useState(defaultOpen)
  return (
    <div className="border border-slate-200 rounded mb-3">
      <button
        onClick={() => setOpen(v => !v)}
        className="w-full flex items-center justify-between px-4 py-3 hover:bg-slate-50 transition-colors"
      >
        <div className="flex items-center gap-2">
          {open ? <ChevronDown size={13} className="text-slate-400" /> : <ChevronRight size={13} className="text-slate-400" />}
          <span className="text-sm font-medium text-slate-700">{title}</span>
          <span className="bg-slate-100 text-slate-500 text-xs font-mono px-1.5 py-0.5 rounded">
            {count}
          </span>
        </div>
        {badge}
      </button>
      {open && <div className="border-t border-slate-100 px-4 py-3">{children}</div>}
    </div>
  )
}

// ── Extraction result panel ───────────────────────────────────────────────────

function ResultPanel({ result, onPersist, persisting, persisted }) {
  const { entities, confidence, extraction_method, parts_found, suppliers_found, relationships_found } = result

  return (
    <div>
      {/* Meta bar */}
      <div className="flex items-center gap-6 mb-4 py-3 px-4 bg-slate-50 border border-slate-200 rounded text-xs">
        <div className="flex items-center gap-1.5 text-green-600">
          <Sparkles size={13} />
          <span className="font-medium">Claude extracted</span>
        </div>
        <div className="text-slate-500">
          <span className="font-medium text-slate-700">{parts_found}</span> parts ·{' '}
          <span className="font-medium text-slate-700">{suppliers_found}</span> suppliers ·{' '}
          <span className="font-medium text-slate-700">{relationships_found}</span> relationships
        </div>
        <div className="flex items-center gap-2 ml-auto w-36">
          <span className="text-slate-400 shrink-0">Confidence</span>
          <ConfidenceMeter value={confidence} />
        </div>
        <div className="text-slate-400 font-mono">{extraction_method}</div>
      </div>

      {/* Parts */}
      {entities.parts?.length > 0 && (
        <Section title="Parts" count={entities.parts.length}>
          <table className="data-table">
            <thead><tr>
              <th>Part ID</th><th>Name</th><th>Category</th><th>Specifications</th>
            </tr></thead>
            <tbody>
              {entities.parts.map((p, i) => (
                <tr key={i}>
                  <td><MonoId value={p.part_id || '—'} /></td>
                  <td className="font-medium text-slate-800 text-sm">{p.name}</td>
                  <td className="text-xs text-slate-500">{p.category || '—'}</td>
                  <td>
                    <div className="flex flex-wrap gap-1">
                      {Object.entries(p.specifications || {}).slice(0, 4).map(([k, v]) => (
                        <span key={k} className="font-mono text-xs bg-slate-100 text-slate-600 px-1.5 py-0.5 rounded">
                          {k}: {String(v)}
                        </span>
                      ))}
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </Section>
      )}

      {/* Suppliers */}
      {entities.suppliers?.length > 0 && (
        <Section title="Suppliers" count={entities.suppliers.length}>
          <table className="data-table">
            <thead><tr>
              <th>Name</th><th>Location</th><th>Certifications</th><th>Contact</th>
            </tr></thead>
            <tbody>
              {entities.suppliers.map((s, i) => (
                <tr key={i}>
                  <td className="font-medium text-slate-800 text-sm">{s.name}</td>
                  <td className="text-xs text-slate-500">{s.location || '—'}</td>
                  <td>
                    <div className="flex flex-wrap gap-1">
                      {(s.certifications || []).map(c => (
                        <span key={c} className="font-mono text-xs bg-blue-50 text-blue-600 px-1.5 py-0.5 rounded">{c}</span>
                      ))}
                    </div>
                  </td>
                  <td className="text-xs text-slate-400 font-mono">
                    {s.contact_info?.email || '—'}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </Section>
      )}

      {/* Relationships */}
      {entities.relationships?.length > 0 && (
        <Section title="Supply relationships" count={entities.relationships.length}>
          <table className="data-table">
            <thead><tr>
              <th>Supplier</th><th>Part ID</th><th>Price</th><th>Lead time</th><th>Currency</th>
            </tr></thead>
            <tbody>
              {entities.relationships.map((r, i) => (
                <tr key={i}>
                  <td className="text-sm">{r.supplier_name}</td>
                  <td><MonoId value={r.part_id} /></td>
                  <td className="font-mono text-sm">{r.price != null ? `$${r.price.toFixed(2)}` : '—'}</td>
                  <td className="text-sm">{r.lead_time_days != null ? `${r.lead_time_days}d` : '—'}</td>
                  <td className="text-xs text-slate-400">{r.currency || 'USD'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </Section>
      )}

      {/* Persist action */}
      <div className="flex items-center justify-end gap-3 mt-4 pt-4 border-t border-slate-200">
        {persisted && (
          <div className="flex items-center gap-1.5 text-green-600 text-sm">
            <CheckCircle size={14} />
            <span>Saved to graph</span>
          </div>
        )}
        {!persisted && (
          <button
            className="btn-primary"
            onClick={onPersist}
            disabled={persisting}
          >
            {persisting
              ? <><Spinner size={13} /> Saving…</>
              : <><CheckCircle size={13} /> Save to graph</>}
          </button>
        )}
      </div>
    </div>
  )
}

// ── Extraction page ───────────────────────────────────────────────────────────

export default function Extraction() {
  const [text,         setText]         = useState('')
  const [documentType, setDocumentType] = useState('catalog')
  const [source,       setSource]       = useState('')
  const [loading,      setLoading]      = useState(false)
  const [result,       setResult]       = useState(null)
  const [error,        setError]        = useState('')
  const [persisting,   setPersisting]   = useState(false)
  const [persisted,    setPersisted]    = useState(false)
  const fileRef = useRef()

  function handleFile(e) {
    const file = e.target.files?.[0]
    if (!file) return
    setSource(file.name)
    const reader = new FileReader()
    reader.onload = ev => setText(ev.target.result)
    reader.readAsText(file)
  }

  async function handleExtract() {
    if (!text.trim()) return
    setLoading(true); setError(''); setResult(null); setPersisted(false)
    try {
      const res = await extractEntities({
        text,
        document_type: documentType,
        source: source || 'manual_input',
        persist: false,
      })
      setResult(res)
    } catch (err) {
      setError(err.message)
    } finally {
      setLoading(false)
    }
  }

  async function handlePersist() {
    if (!text.trim()) return
    setPersisting(true); setError('')
    try {
      const res = await extractEntities({
        text,
        document_type: documentType,
        source: source || 'manual_input',
        persist: true,
      })
      setResult(res)
      setPersisted(true)
    } catch (err) {
      setError(err.message)
    } finally {
      setPersisting(false)
    }
  }

  return (
    <div className="p-6 max-w-4xl">
      {/* Header */}
      <div className="flex items-center gap-2 mb-2">
        <Sparkles size={16} className="text-violet-500" />
        <h1 className="text-sm font-semibold text-slate-700 uppercase tracking-wider">
          Extract with Claude
        </h1>
      </div>
      <p className="text-sm text-slate-500 mb-6">
        Paste a supplier catalog, BOM, or price list. Claude will identify parts,
        suppliers, and supply relationships — then you can save them to the graph.
      </p>

      {/* Input area */}
      <div className="panel mb-4">
        <div className="p-4 space-y-3">
          {/* Controls row */}
          <div className="flex gap-2 items-end">
            <div className="w-40">
              <label className="field-label">Document type</label>
              <select className="field" value={documentType} onChange={e => setDocumentType(e.target.value)}>
                {['catalog', 'bom', 'price_list', 'purchase_order', 'unknown'].map(t => (
                  <option key={t}>{t}</option>
                ))}
              </select>
            </div>
            <div className="flex-1">
              <label className="field-label">Source / filename</label>
              <input className="field font-mono text-xs" value={source}
                onChange={e => setSource(e.target.value)} placeholder="optional — e.g. acme_catalog_2024.pdf" />
            </div>
            <div className="flex gap-2 pb-0.5">
              <button
                type="button"
                className="btn-secondary"
                onClick={() => fileRef.current?.click()}
              >
                <Upload size={13} /> Upload file
              </button>
              <button
                type="button"
                className="btn-secondary text-xs"
                onClick={() => { setText(SAMPLE_TEXT); setDocumentType('catalog'); setSource('sample_catalog.txt') }}
              >
                <FileText size={13} /> Load sample
              </button>
              <input ref={fileRef} type="file" accept=".txt,.md,.csv" className="hidden" onChange={handleFile} />
            </div>
          </div>

          {/* Text area */}
          <textarea
            className="field font-mono text-xs resize-none"
            rows={14}
            value={text}
            onChange={e => { setText(e.target.value); setResult(null); setPersisted(false) }}
            placeholder="Paste supplier catalog, BOM, price list, or any document with part and supplier information…"
          />

          {/* Extract button */}
          <div className="flex items-center justify-between">
            <span className="text-xs text-slate-400">
              {text.length > 0 ? `${text.length.toLocaleString()} characters` : ''}
            </span>
            <button
              className="btn-primary"
              onClick={handleExtract}
              disabled={loading || !text.trim()}
            >
              {loading
                ? <><Spinner size={13} /> Claude is reading…</>
                : <><Sparkles size={13} /> Extract entities</>}
            </button>
          </div>
        </div>
      </div>

      <ErrorBanner message={error} onDismiss={() => setError('')} />

      {/* Loading state */}
      {loading && (
        <div className="flex flex-col items-center justify-center py-16 text-slate-400 gap-3">
          <Spinner size={20} />
          <p className="text-sm">Claude is analysing the document…</p>
        </div>
      )}

      {/* Results */}
      {result && !loading && (
        <div className="panel p-4">
          <ResultPanel
            result={result}
            onPersist={handlePersist}
            persisting={persisting}
            persisted={persisted}
          />
        </div>
      )}
    </div>
  )
}