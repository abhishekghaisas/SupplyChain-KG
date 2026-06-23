import { useState, useEffect, useCallback } from 'react'
import { Plus, ChevronRight, RefreshCw } from 'lucide-react'
import { parts as api } from '../api/client'
import {
  CriticalityBadge, PageSpinner, EmptyState, ErrorBanner,
  Modal, Field, SectionHeader, MonoId,
} from '../components/ui'
import SearchBar from '../components/SearchBar'

// ── Create part form ──────────────────────────────────────────────────────────

function CreatePartModal({ onClose, onCreated }) {
  const [form, setForm] = useState({
    id: '', name: '', description: '', category: 'electronic',
    criticality: 'MEDIUM', unit_of_measure: 'EA',
  })
  const [error, setError] = useState('')
  const [saving, setSaving] = useState(false)

  function set(k, v) { setForm(f => ({ ...f, [k]: v })) }

  async function handleSubmit(e) {
    e.preventDefault()
    setSaving(true)
    setError('')
    try {
      await api.create(form)
      onCreated()
      onClose()
    } catch (err) {
      setError(err.message)
      setSaving(false)
    }
  }

  return (
    <Modal title="Add part" onClose={onClose}>
      <form onSubmit={handleSubmit}>
        <ErrorBanner message={error} onDismiss={() => setError('')} />
        <div className="grid grid-cols-2 gap-x-4">
          <Field label="Part ID *">
            <input className="field font-mono" value={form.id}
              onChange={e => set('id', e.target.value)} placeholder="P-12345" required />
          </Field>
          <Field label="Unit of measure">
            <input className="field" value={form.unit_of_measure}
              onChange={e => set('unit_of_measure', e.target.value)} />
          </Field>
        </div>
        <Field label="Name *">
          <input className="field" value={form.name}
            onChange={e => set('name', e.target.value)} required />
        </Field>
        <Field label="Description">
          <textarea className="field" rows={2} value={form.description}
            onChange={e => set('description', e.target.value)} />
        </Field>
        <div className="grid grid-cols-2 gap-x-4">
          <Field label="Category">
            <select className="field" value={form.category}
              onChange={e => set('category', e.target.value)}>
              {['electronic', 'electrical', 'electromechanical', 'mechanical', 'hydraulic', 'pneumatic', 'software', 'raw_material', 'other'].map(c => (
                <option key={c}>{c}</option>
              ))}
            </select>
          </Field>
          <Field label="Criticality">
            <select className="field" value={form.criticality}
              onChange={e => set('criticality', e.target.value)}>
              {['LOW', 'MEDIUM', 'HIGH', 'CRITICAL'].map(c => (
                <option key={c}>{c}</option>
              ))}
            </select>
          </Field>
        </div>
        <div className="flex justify-end gap-2 mt-2">
          <button type="button" className="btn-secondary" onClick={onClose}>Cancel</button>
          <button type="submit" className="btn-primary" disabled={saving}>
            {saving ? 'Saving…' : 'Add part'}
          </button>
        </div>
      </form>
    </Modal>
  )
}

// ── Part detail panel ─────────────────────────────────────────────────────────

function PartDetail({ partId, onClose }) {
  const [part,     setPart]     = useState(null)
  const [partSuppliers, setPartSuppliers] = useState([])
  const [compat,   setCompat]   = useState([])
  const [tab,      setTab]      = useState('overview')
  const [loading,  setLoading]  = useState(true)

  useEffect(() => {
    Promise.all([
      api.get(partId),
      api.suppliers(partId),
      api.compatibility(partId),
    ]).then(([p, s, c]) => {
      setPart(p); setPartSuppliers(s); setCompat(c)
    }).finally(() => setLoading(false))
  }, [partId])

  if (loading) return <Modal title="Part detail" onClose={onClose}><PageSpinner /></Modal>
  if (!part)   return null

  return (
    <Modal title={part.name} onClose={onClose} width="max-w-2xl">
      {/* Tabs */}
      <div className="flex gap-4 border-b border-slate-200 mb-4 -mt-1">
        {['overview', 'suppliers', 'compatibility'].map(t => (
          <button key={t} onClick={() => setTab(t)}
            className={`pb-2 text-xs font-medium capitalize border-b-2 transition-colors
              ${tab === t ? 'border-slate-800 text-slate-800' : 'border-transparent text-slate-400 hover:text-slate-600'}`}>
            {t}
          </button>
        ))}
      </div>

      {tab === 'overview' && (
        <div className="space-y-3 text-sm">
          <div className="flex gap-6">
            <div><span className="text-slate-400 text-xs">ID</span><div><MonoId value={part.id} /></div></div>
            <div><span className="text-slate-400 text-xs">Category</span><div>{part.category}</div></div>
            <div><span className="text-slate-400 text-xs">Criticality</span><div><CriticalityBadge value={part.criticality} /></div></div>
            <div><span className="text-slate-400 text-xs">UOM</span><div>{part.unit_of_measure}</div></div>
          </div>
          {part.description && <p className="text-slate-600">{part.description}</p>}
          {Object.keys(part.specifications || {}).length > 0 && (
            <div>
              <p className="text-xs font-medium text-slate-400 uppercase tracking-wider mb-2">Specifications</p>
              <div className="bg-slate-50 rounded p-3 font-mono text-xs text-slate-700 space-y-1">
                {Object.entries(part.specifications).map(([k, v]) => (
                  <div key={k}><span className="text-slate-400">{k}:</span> {JSON.stringify(v)}</div>
                ))}
              </div>
            </div>
          )}
        </div>
      )}

      {tab === 'suppliers' && (
        partSuppliers.length === 0
          ? <EmptyState message="No active suppliers for this part" />
          : <table className="data-table">
              <thead><tr>
                <th>Supplier</th><th>Lead time</th><th>Price</th><th>On-time %</th>
              </tr></thead>
              <tbody>
                {partSuppliers.map(s => (
                  <tr key={s.supplier_id}>
                    <td><MonoId value={s.supplier_id} /><div className="text-sm">{s.supplier_name}</div></td>
                    <td>{s.lead_time_days}d</td>
                    <td>${s.price?.toFixed(2)} {s.currency}</td>
                    <td>{s.on_time_delivery_rate != null ? `${(s.on_time_delivery_rate * 100).toFixed(0)}%` : '—'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
      )}

      {tab === 'compatibility' && (
        <>
          <div className="mb-4">
            <SubstituteSuggestions partId={partId} />
          </div>
          {compat.length === 0
            ? <EmptyState message="No verified substitutes on record — use AI above to find candidates" />
            : <table className="data-table">
              <thead><tr>
                <th>Substitute ID</th><th>Type</th><th>Status</th><th>Notes</th>
              </tr></thead>
              <tbody>
                {compat.map(c => (
                  <tr key={c.substitute_part_id}>
                    <td><MonoId value={c.substitute_part_id} /></td>
                    <td className="text-xs">{c.compatibility_type}</td>
                    <td><span className="badge badge-released">{c.validation_status}</span></td>
                    <td className="text-xs text-slate-500">{c.notes || '—'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          }
        </>
      )}
    </Modal>
  )
}

// ── Parts page ────────────────────────────────────────────────────────────────

export default function Parts() {
  const [data,       setData]       = useState([])
  const [loading,    setLoading]    = useState(true)
  const [error,      setError]      = useState('')
  const [category,   setCategory]   = useState('')
  const [criticality,setCriticality]= useState('')
  const [showCreate, setShowCreate] = useState(false)
  const [searchResults, setSearchResults] = useState(null)
  const [selected,   setSelected]   = useState(null)

  const load = useCallback(async () => {
    setLoading(true)
    setError('')
    try {
      const params = {}
      if (category)    params.category    = category
      if (criticality) params.criticality = criticality
      setData(await api.list(params))
    } catch (err) {
      setError(err.message)
    } finally {
      setLoading(false)
    }
  }, [category, criticality])

  useEffect(() => { load() }, [load])

  return (
    <div className="p-6">
      <SectionHeader
        title={`Parts ${data.length ? `(${data.length})` : ''}`}
        action={
          <div className="flex items-center gap-2">
            <select className="field text-xs py-1.5 w-36" value={category} onChange={e => setCategory(e.target.value)}>
              <option value="">All categories</option>
              {['electronic', 'electrical', 'electromechanical', 'mechanical', 'hydraulic', 'pneumatic', 'software', 'raw_material', 'other'].map(c => (
                <option key={c}>{c}</option>
              ))}
            </select>
            <select className="field text-xs py-1.5 w-36" value={criticality} onChange={e => setCriticality(e.target.value)}>
              <option value="">All criticality</option>
              {['LOW','MEDIUM','HIGH','CRITICAL'].map(c => <option key={c}>{c}</option>)}
            </select>
            <button className="btn-secondary" onClick={load}><RefreshCw size={13} /></button>
            <button className="btn-primary" onClick={() => setShowCreate(true)}>
              <Plus size={13} /> Add part
            </button>
          </div>
        }
      />

      <ErrorBanner message={error} onDismiss={() => setError('')} />

      <div className="mb-4">
        <SearchBar
          entityType="part"
          onResults={(results, q) => setSearchResults(q ? results : null)}
          placeholder="Search by name, specs… or ID prefix like P-123"
        />
      </div>

      <div className="panel">
        {loading ? <PageSpinner /> : searchResults !== null ? (
          searchResults.length === 0
            ? <EmptyState message="No parts match your search" />
            : <table className="data-table">
                <thead><tr><th>ID</th><th>Name</th><th>Category</th><th>Criticality</th><th>Score</th><th></th></tr></thead>
                <tbody>
                  {searchResults.map(r => (
                    <tr key={r.entity_id} className="cursor-pointer" onClick={() => setSelected(r.entity_id)}>
                      <td><MonoId value={r.entity_id} /></td>
                      <td className="font-medium text-slate-800">{r.name}</td>
                      <td className="text-slate-500 text-xs">{r.data?.category || '—'}</td>
                      <td>{r.data?.criticality ? <CriticalityBadge value={r.data.criticality} /> : '—'}</td>
                      <td className="font-mono text-xs text-slate-400">{r.score !== null ? r.score?.toFixed(2) : '—'}</td>
                      <td><ChevronRight size={14} className="text-slate-300" /></td>
                    </tr>
                  ))}
                </tbody>
              </table>
        ) : data.length === 0 ? (
          <EmptyState message="No parts found"
            action={<button className="btn-primary" onClick={() => setShowCreate(true)}><Plus size={13} />Add first part</button>} />
        ) : (
          <table className="data-table">
            <thead><tr>
              <th>ID</th><th>Name</th><th>Category</th><th>Criticality</th><th>UOM</th><th></th>
            </tr></thead>
            <tbody>
              {data.map(p => (
                <tr key={p.id} className="cursor-pointer" onClick={() => setSelected(p.id)}>
                  <td><MonoId value={p.id} /></td>
                  <td className="font-medium text-slate-800">{p.name}</td>
                  <td className="text-slate-500 text-xs">{p.category}</td>
                  <td><CriticalityBadge value={p.criticality} /></td>
                  <td className="text-slate-500 text-xs">{p.unit_of_measure}</td>
                  <td><ChevronRight size={14} className="text-slate-300" /></td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>

      {showCreate && (
        <CreatePartModal onClose={() => setShowCreate(false)} onCreated={load} />
      )}
      {selected && (
        <PartDetail partId={selected} onClose={() => setSelected(null)} />
      )}
    </div>
  )
}