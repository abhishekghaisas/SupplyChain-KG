import AIReview from '../components/AIReview'
import SearchBar from '../components/SearchBar'
import { useState, useEffect, useCallback } from 'react'
import { Plus, RefreshCw, ChevronRight, Zap, Sparkles } from 'lucide-react'
import { suppliers as api, disruption as disruptionApi } from '../api/client'
import {
  PageSpinner, EmptyState, ErrorBanner, Modal,
  Field, SectionHeader, MonoId, SeverityBadge,
} from '../components/ui'

// ── Create supplier form ──────────────────────────────────────────────────────

function CreateSupplierModal({ onClose, onCreated }) {
  const [form, setForm] = useState({
    id: '', name: '', location: '', certifications: '',
    status: 'ACTIVE', tier: 2, rating: 4.0,
  })
  const [error, setError] = useState('')
  const [saving, setSaving] = useState(false)

  function set(k, v) { setForm(f => ({ ...f, [k]: v })) }

  async function handleSubmit(e) {
    e.preventDefault()
    setSaving(true)
    setError('')
    try {
      await api.create({
        ...form,
        tier:   Number(form.tier),
        rating: Number(form.rating),
        certifications: form.certifications.split(',').map(s => s.trim()).filter(Boolean),
      })
      onCreated()
      onClose()
    } catch (err) {
      setError(err.message)
      setSaving(false)
    }
  }

  return (
    <Modal title="Add supplier" onClose={onClose}>
      <form onSubmit={handleSubmit}>
        <ErrorBanner message={error} onDismiss={() => setError('')} />
        <div className="grid grid-cols-2 gap-x-4">
          <Field label="Supplier ID *">
            <input className="field font-mono" value={form.id}
              onChange={e => set('id', e.target.value)} placeholder="SUP-001" required />
          </Field>
          <Field label="Status">
            <select className="field" value={form.status} onChange={e => set('status', e.target.value)}>
              <option>ACTIVE</option><option>INACTIVE</option><option>PROBATION</option>
            </select>
          </Field>
        </div>
        <Field label="Name *">
          <input className="field" value={form.name}
            onChange={e => set('name', e.target.value)} required />
        </Field>
        <Field label="Location">
          <input className="field" value={form.location}
            onChange={e => set('location', e.target.value)} placeholder="Germany" />
        </Field>
        <Field label="Certifications" hint="Comma-separated, e.g. ISO9001, IATF16949">
          <input className="field" value={form.certifications}
            onChange={e => set('certifications', e.target.value)} />
        </Field>
        <div className="grid grid-cols-2 gap-x-4">
          <Field label="Tier">
            <select className="field" value={form.tier} onChange={e => set('tier', e.target.value)}>
              <option value={1}>Tier 1</option><option value={2}>Tier 2</option><option value={3}>Tier 3</option>
            </select>
          </Field>
          <Field label="Rating (0–5)">
            <input className="field" type="number" min="0" max="5" step="0.1"
              value={form.rating} onChange={e => set('rating', e.target.value)} />
          </Field>
        </div>
        <div className="flex justify-end gap-2 mt-2">
          <button type="button" className="btn-secondary" onClick={onClose}>Cancel</button>
          <button type="submit" className="btn-primary" disabled={saving}>
            {saving ? 'Saving…' : 'Add supplier'}
          </button>
        </div>
      </form>
    </Modal>
  )
}

// ── Disruption panel ──────────────────────────────────────────────────────────

function DisruptionPanel({ supplierId, onClose }) {
  const [report,  setReport]  = useState(null)
  const [loading, setLoading] = useState(true)
  const [error,   setError]   = useState('')

  useEffect(() => {
    disruptionApi.supplier(supplierId, 'RELEASED,REVIEW,DRAFT')
      .then(setReport)
      .catch(err => setError(err.message))
      .finally(() => setLoading(false))
  }, [supplierId])

  const ACTION_COLOR = {
    ESCALATE:           'text-red-600',
    USE_SUBSTITUTE:     'text-green-600',
    EXPEDITE_ALTERNATE: 'text-blue-600',
    DUAL_SOURCE:        'text-amber-600',
    MONITOR:            'text-slate-500',
  }

  return (
    <Modal title={`Disruption analysis — ${supplierId}`} onClose={onClose} width="max-w-3xl">
      {loading ? <PageSpinner /> : error ? <ErrorBanner message={error} /> : (
        <>
          <div className="mb-4 text-sm text-slate-600">{report.summary}</div>

          {report.affected_boms.length === 0 ? (
            <EmptyState message="No BOMs affected in the selected statuses" />
          ) : (
            <div className="space-y-4">
              {report.affected_boms
                .sort((a, b) => b.severity_score - a.severity_score)
                .map(bom => (
                  <div key={bom.bom_id} className="panel">
                    <div className="panel-header">
                      <div className="flex items-center gap-3">
                        <SeverityBadge label={bom.severity_label} />
                        <span className="panel-title">{bom.bom_name}</span>
                        <MonoId value={bom.bom_id} />
                      </div>
                      <span className="text-xs text-slate-400">
                        score: {bom.severity_score.toFixed(2)}
                      </span>
                    </div>
                    <div className="px-4 py-3">
                      <div className="flex flex-wrap gap-1.5 mb-3">
                        {bom.actions.map(a => (
                          <span key={a}
                            className={`text-xs font-medium font-mono ${ACTION_COLOR[a] ?? 'text-slate-500'}`}>
                            {a}
                          </span>
                        ))}
                      </div>
                      <table className="data-table">
                        <thead><tr>
                          <th>Part</th><th>Criticality</th><th>Qty</th>
                          <th>Alternates</th><th>Substitute</th>
                        </tr></thead>
                        <tbody>
                          {bom.disrupted_parts.map(dp => (
                            <tr key={dp.part_id}>
                              <td>
                                <MonoId value={dp.part_id} />
                                <div className="text-xs text-slate-600">{dp.part_name}</div>
                              </td>
                              <td><span className={`badge badge-${dp.criticality.toLowerCase()}`}>{dp.criticality}</span></td>
                              <td>{dp.quantity_in_bom}</td>
                              <td>{dp.alternate_supplier_count}</td>
                              <td>
                                {dp.has_substitute
                                  ? <span className="text-xs text-green-600">✓ {dp.substitutes[0]?.part_id}</span>
                                  : <span className="text-xs text-slate-400">None</span>}
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
        </>
      )}
    </Modal>
  )
}

// ── Suppliers page ────────────────────────────────────────────────────────────

export default function Suppliers() {
  const [data,       setData]       = useState([])
  const [loading,    setLoading]    = useState(true)
  const [error,      setError]      = useState('')
  const [showCreate, setShowCreate] = useState(false)
  const [searchResults, setSearchResults] = useState(null)
  const [disruption, setDisruption] = useState(null)
  const [qualifying,  setQualifying]  = useState(null)

  const load = useCallback(async () => {
    setLoading(true)
    setError('')
    try { setData(await api.list()) }
    catch (err) { setError(err.message) }
    finally { setLoading(false) }
  }, [])

  useEffect(() => { load() }, [load])

  return (
    <div className="p-6">
      <SectionHeader
        title={`Suppliers ${data.length ? `(${data.length})` : ''}`}
        action={
          <div className="flex items-center gap-2">
            <button className="btn-secondary" onClick={load}><RefreshCw size={13} /></button>
            <button className="btn-primary" onClick={() => setShowCreate(true)}>
              <Plus size={13} /> Add supplier
            </button>
          </div>
        }
      />

      <ErrorBanner message={error} onDismiss={() => setError('')} />

      <div className="mb-4">
        <SearchBar
          entityType="supplier"
          onResults={(results, q) => setSearchResults(q ? results : null)}
          placeholder="Search by name, location… or ID prefix like SUP-0"
        />
      </div>

      <div className="panel">
        {loading ? <PageSpinner /> : searchResults !== null ? (
          searchResults.length === 0
            ? <EmptyState message="No suppliers match your search" />
            : <table className="data-table">
                <thead><tr><th>ID</th><th>Name</th><th>Location</th><th>Score</th></tr></thead>
                <tbody>
                  {searchResults.map(r => (
                    <tr key={r.entity_id}>
                      <td><MonoId value={r.entity_id} /></td>
                      <td className="font-medium text-slate-800">{r.name}</td>
                      <td className="text-slate-500 text-xs">{r.data?.location || '—'}</td>
                      <td className="font-mono text-xs text-slate-400">{r.score !== null ? r.score?.toFixed(2) : '—'}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
        ) : data.length === 0 ? (
          <EmptyState message="No suppliers found"
            action={<button className="btn-primary" onClick={() => setShowCreate(true)}><Plus size={13} />Add first supplier</button>} />
        ) : (
          <table className="data-table">
            <thead><tr>
              <th>ID</th><th>Name</th><th>Location</th><th>Tier</th>
              <th>Rating</th><th>Certifications</th><th>Status</th><th></th>
            </tr></thead>
            <tbody>
              {data.map(s => (
                <tr key={s.id}>
                  <td><MonoId value={s.id} /></td>
                  <td className="font-medium text-slate-800">{s.name}</td>
                  <td className="text-slate-500 text-xs">{s.location}</td>
                  <td className="text-slate-500 text-xs">{s.tier ?? '—'}</td>
                  <td className="text-slate-500 text-xs">{s.rating?.toFixed(1) ?? '—'}</td>
                  <td>
                    <div className="flex flex-wrap gap-1">
                      {(s.certifications || []).map(c => (
                        <span key={c} className="font-mono text-xs bg-slate-100 text-slate-600 px-1.5 py-0.5 rounded">{c}</span>
                      ))}
                    </div>
                  </td>
                  <td>
                    <span className={`badge ${s.status === 'ACTIVE' ? 'badge-released' : 'badge-rejected'}`}>
                      {s.status}
                    </span>
                  </td>
                  <td>
                    <div className="flex items-center gap-3">
                      <button
                        onClick={() => setDisruption(s.id)}
                        className="flex items-center gap-1 text-xs text-amber-600 hover:text-amber-800"
                        title="Run disruption analysis"
                      >
                        <Zap size={12} /> Disruption
                      </button>
                      <button
                        onClick={() => setQualifying(s.id)}
                        className="flex items-center gap-1 text-xs text-violet-600 hover:text-violet-800"
                        title="AI qualification memo"
                      >
                        <Sparkles size={12} /> AI Qualify
                      </button>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>

      {showCreate && (
        <CreateSupplierModal onClose={() => setShowCreate(false)} onCreated={load} />
      )}
      {disruption && (
        <DisruptionPanel supplierId={disruption} onClose={() => setDisruption(null)} />
      )}
      {qualifying && (
        <Modal title={`AI Qualification — ${qualifying}`} onClose={() => setQualifying(null)} width="max-w-2xl">
          <AIReview
            title="Supplier Qualification Memo"
            buttonLabel="Generate Qualification Memo"
            onGenerate={() => api.aiQualify(qualifying)}
          />
        </Modal>
      )}
    </div>
  )
}