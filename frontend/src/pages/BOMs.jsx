import { useState, useEffect, useCallback } from 'react'
import { Plus, RefreshCw, GitBranch, GitCompare, CheckCircle, ArrowRight, ChevronDown } from 'lucide-react'
import { boms as api, parts as partsApi } from '../api/client'
import {
  StatusBadge, CriticalityBadge, PageSpinner, EmptyState,
  ErrorBanner, Modal, Field, SectionHeader, MonoId, Spinner,
} from '../components/ui'
import AIReview from '../components/AIReview'

// ── Create BOM form ───────────────────────────────────────────────────────────

function CreateBOMModal({ onClose, onCreated }) {
  const [form, setForm]       = useState({ id: '', name: '', description: '', version: '1.0', status: 'DRAFT' })
  const [components, setComponents] = useState([])   // inline components
  const [partSearch, setPartSearch] = useState('')
  const [partResults, setPartResults] = useState([])
  const [searching, setSearching]   = useState(false)
  const [compForm, setCompForm]     = useState({ part_id: '', part_name: '', quantity: 1, reference_designator: '', unit_of_measure: 'EA' })
  const [error, setError]     = useState('')
  const [saving, setSaving]   = useState(false)

  function set(k, v) { setForm(f => ({ ...f, [k]: v })) }
  function setComp(k, v) { setCompForm(f => ({ ...f, [k]: v })) }

  // Search parts as user types
  async function handlePartSearch(q) {
    setPartSearch(q)
    if (!q.trim()) { setPartResults([]); return }
    setSearching(true)
    try {
      const { request } = await import('../api/client')
      const isId = /^[A-Za-z0-9]+-/.test(q)
      const url  = isId
        ? `/parts?id_prefix=${encodeURIComponent(q)}&limit=8`
        : `/search?q=${encodeURIComponent(q)}&type=part&limit=8`
      const data = await request(url)
      // Normalise both list and search results
      const rows = Array.isArray(data) ? data : (data.results || [])
      setPartResults(rows.map(r => ({
        id:   r.id || r.entity_id,
        name: r.name,
        criticality: r.criticality || r.data?.criticality,
      })))
    } catch {}
    finally { setSearching(false) }
  }

  function selectPart(part) {
    setCompForm(f => ({ ...f, part_id: part.id, part_name: part.name }))
    setPartSearch(part.id)
    setPartResults([])
  }

  function addComponent() {
    if (!compForm.part_id || !compForm.quantity) return
    // Prevent duplicates
    if (components.find(c => c.part_id === compForm.part_id)) return
    setComponents(prev => [...prev, { ...compForm, quantity: Number(compForm.quantity) }])
    setCompForm({ part_id: '', part_name: '', quantity: 1, reference_designator: '', unit_of_measure: 'EA' })
    setPartSearch('')
  }

  function removeComponent(part_id) {
    setComponents(prev => prev.filter(c => c.part_id !== part_id))
  }

  async function handleSubmit(e) {
    e.preventDefault(); setSaving(true); setError('')
    try {
      await api.create({ ...form, components })
      onCreated(); onClose()
    } catch (err) { setError(err.message); setSaving(false) }
  }

  return (
    <Modal title="Create BOM" onClose={onClose} width="max-w-2xl">
      <form onSubmit={handleSubmit}>
        <ErrorBanner message={error} onDismiss={() => setError('')} />

        {/* BOM header fields */}
        <div className="grid grid-cols-2 gap-x-4">
          <Field label="BOM ID *">
            <input className="field font-mono" value={form.id}
              onChange={e => set('id', e.target.value)} placeholder="BOM-001" required />
          </Field>
          <Field label="Version">
            <input className="field font-mono" value={form.version}
              onChange={e => set('version', e.target.value)} />
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
        <Field label="Status">
          <select className="field" value={form.status} onChange={e => set('status', e.target.value)}>
            {['DRAFT','REVIEW','RELEASED','ARCHIVED'].map(s => <option key={s}>{s}</option>)}
          </select>
        </Field>

        {/* Component section */}
        <div className="mt-4 pt-4 border-t border-slate-200">
          <p className="text-xs font-semibold text-slate-500 uppercase tracking-wider mb-3">
            Components ({components.length})
          </p>

          {/* Component list */}
          {components.length > 0 && (
            <div className="mb-3 border border-slate-200 rounded overflow-hidden">
              <table className="data-table">
                <thead><tr>
                  <th>Part ID</th><th>Name</th><th>Qty</th><th>Ref</th><th></th>
                </tr></thead>
                <tbody>
                  {components.map(c => (
                    <tr key={c.part_id}>
                      <td><MonoId value={c.part_id} /></td>
                      <td className="text-sm text-slate-700">{c.part_name}</td>
                      <td className="text-sm">{c.quantity} {c.unit_of_measure}</td>
                      <td className="font-mono text-xs text-slate-400">{c.reference_designator || '—'}</td>
                      <td>
                        <button type="button" onClick={() => removeComponent(c.part_id)}
                          className="text-xs text-red-400 hover:text-red-600">×</button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}

          {/* Add component row */}
          <div className="bg-slate-50 border border-slate-200 rounded p-3">
            <p className="text-xs text-slate-500 mb-2">Add a part</p>
            <div className="flex gap-2 mb-2 relative">
              <div className="flex-1 relative">
                <input
                  className="field text-xs w-full"
                  value={partSearch}
                  onChange={e => handlePartSearch(e.target.value)}
                  placeholder="Search by name or ID…"
                />
                {partResults.length > 0 && (
                  <div className="absolute z-20 top-full left-0 right-0 mt-1 bg-white border
                                  border-slate-200 rounded shadow-lg max-h-40 overflow-y-auto">
                    {partResults.map(p => (
                      <button key={p.id} type="button" onClick={() => selectPart(p)}
                        className="w-full flex items-center gap-2 px-3 py-2 text-left
                                   hover:bg-slate-50 border-b border-slate-100 last:border-0">
                        <span className="font-mono text-xs text-slate-400 w-28 shrink-0 truncate">{p.id}</span>
                        <span className="text-sm text-slate-700 truncate">{p.name}</span>
                      </button>
                    ))}
                  </div>
                )}
                {searching && (
                  <div className="absolute right-2 top-2">
                    <Spinner size={12} />
                  </div>
                )}
              </div>
              <input className="field text-xs w-20" type="number" min="0.001" step="any"
                value={compForm.quantity} onChange={e => setComp('quantity', e.target.value)}
                placeholder="Qty" />
              <input className="field text-xs w-20" value={compForm.reference_designator}
                onChange={e => setComp('reference_designator', e.target.value)}
                placeholder="Ref (M1)" />
              <select className="field text-xs w-20" value={compForm.unit_of_measure}
                onChange={e => setComp('unit_of_measure', e.target.value)}>
                {['EA','M','KG','BOX','REEL'].map(u => <option key={u}>{u}</option>)}
              </select>
              <button type="button" onClick={addComponent}
                disabled={!compForm.part_id}
                className="btn-secondary text-xs shrink-0 disabled:opacity-40">
                <Plus size={12} /> Add
              </button>
            </div>
          </div>
        </div>

        <div className="flex justify-end gap-2 mt-4">
          <button type="button" className="btn-secondary" onClick={onClose}>Cancel</button>
          <button type="submit" className="btn-primary" disabled={saving}>
            {saving ? 'Saving…' : `Create BOM${components.length ? ` with ${components.length} component${components.length > 1 ? 's' : ''}` : ''}`}
          </button>
        </div>
      </form>
    </Modal>
  )
}

// ── Clone BOM form ────────────────────────────────────────────────────────────

function CloneBOMModal({ sourceBomId, onClose, onCreated }) {
  const [form, setForm]   = useState({ new_bom_id: '', new_version: '', cloned_by: '' })
  const [error, setError] = useState('')
  const [saving, setSaving] = useState(false)
  function set(k, v) { setForm(f => ({ ...f, [k]: v })) }

  async function handleSubmit(e) {
    e.preventDefault(); setSaving(true); setError('')
    try { await api.clone(sourceBomId, form); onCreated(); onClose() }
    catch (err) { setError(err.message); setSaving(false) }
  }

  return (
    <Modal title={`Clone ${sourceBomId}`} onClose={onClose}>
      <form onSubmit={handleSubmit}>
        <ErrorBanner message={error} onDismiss={() => setError('')} />
        <Field label="New BOM ID *">
          <input className="field font-mono" value={form.new_bom_id}
            onChange={e => set('new_bom_id', e.target.value)} required />
        </Field>
        <Field label="New version *">
          <input className="field font-mono" value={form.new_version}
            onChange={e => set('new_version', e.target.value)} placeholder="2.0" required />
        </Field>
        <Field label="Cloned by">
          <input className="field" value={form.cloned_by}
            onChange={e => set('cloned_by', e.target.value)} placeholder="engineer@example.com" />
        </Field>
        <div className="flex justify-end gap-2 mt-2">
          <button type="button" className="btn-secondary" onClick={onClose}>Cancel</button>
          <button type="submit" className="btn-primary" disabled={saving}>{saving ? 'Cloning…' : 'Clone BOM'}</button>
        </div>
      </form>
    </Modal>
  )
}

// ── Diff viewer ───────────────────────────────────────────────────────────────

function DiffModal({ bomId, onClose }) {
  const [otherId, setOtherId] = useState('')
  const [diff,    setDiff]    = useState(null)
  const [loading, setLoading] = useState(false)
  const [error,   setError]   = useState('')

  async function runDiff() {
    if (!otherId) return
    setLoading(true); setError('')
    try { setDiff(await api.diff(bomId, otherId)) }
    catch (err) { setError(err.message) }
    finally { setLoading(false) }
  }

  return (
    <Modal title={`Diff from ${bomId}`} onClose={onClose} width="max-w-2xl">
      <div className="flex gap-2 mb-4">
        <input className="field flex-1 font-mono" value={otherId}
          onChange={e => setOtherId(e.target.value)} placeholder="Compare to BOM ID…" />
        <button className="btn-primary" onClick={runDiff} disabled={loading || !otherId}>
          {loading ? <Spinner size={13} /> : 'Diff'}
        </button>
      </div>
      <ErrorBanner message={error} onDismiss={() => setError('')} />
      {diff && (
        <>
          <p className="text-sm text-slate-600 mb-4">{diff.summary}</p>
          {diff.added.length > 0 && (
            <div className="mb-4">
              <p className="text-xs font-medium text-green-600 uppercase tracking-wider mb-2">Added ({diff.added.length})</p>
              {diff.added.map(c => (
                <div key={c.part_id} className="flex items-center gap-3 py-1.5 border-b border-slate-100 last:border-0">
                  <span className="text-green-500 font-mono text-xs">+</span>
                  <MonoId value={c.part_id} />
                  <span className="text-sm">{c.part_name}</span>
                  <CriticalityBadge value={c.criticality} />
                  <span className="text-xs text-slate-400 ml-auto">qty {c.quantity}</span>
                </div>
              ))}
            </div>
          )}
          {diff.removed.length > 0 && (
            <div className="mb-4">
              <p className="text-xs font-medium text-red-600 uppercase tracking-wider mb-2">Removed ({diff.removed.length})</p>
              {diff.removed.map(c => (
                <div key={c.part_id} className="flex items-center gap-3 py-1.5 border-b border-slate-100 last:border-0">
                  <span className="text-red-500 font-mono text-xs">−</span>
                  <MonoId value={c.part_id} />
                  <span className="text-sm">{c.part_name}</span>
                  <CriticalityBadge value={c.criticality} />
                </div>
              ))}
            </div>
          )}
          {diff.modified.length > 0 && (
            <div>
              <p className="text-xs font-medium text-amber-600 uppercase tracking-wider mb-2">Modified ({diff.modified.length})</p>
              {diff.modified.map(c => (
                <div key={c.part_id} className="py-1.5 border-b border-slate-100 last:border-0">
                  <div className="flex items-center gap-3 mb-1">
                    <span className="text-amber-500 font-mono text-xs">~</span>
                    <MonoId value={c.part_id} />
                    <span className="text-sm">{c.part_name}</span>
                  </div>
                  {Object.entries(c.changes).map(([field, delta]) => (
                    <div key={field} className="ml-6 text-xs text-slate-500 flex items-center gap-2">
                      <span className="font-mono">{field}:</span>
                      <span className="text-red-500">{JSON.stringify(delta.from)}</span>
                      <ArrowRight size={10} />
                      <span className="text-green-500">{JSON.stringify(delta.to)}</span>
                    </div>
                  ))}
                </div>
              ))}
            </div>
          )}
          {!diff.has_changes && <EmptyState message="No differences found" />}
        </>
      )}
    </Modal>
  )
}

// ── BOM detail / approval ─────────────────────────────────────────────────────

function BOMDetail({ bomId, onClose, onUpdated }) {
  const [bom,         setBom]         = useState(null)
  const [transitions, setTransitions] = useState([])
  const [approval,    setApproval]    = useState(null)
  const [tab,         setTab]         = useState('components')
  const [loading,     setLoading]     = useState(true)
  const [approving,   setApproving]   = useState(false)
  const [approver,    setApprover]    = useState('')
  const [transitioning, setTransitioning] = useState(false)
  const [error,       setError]       = useState('')

  const load = useCallback(async () => {
    const [b, t, a] = await Promise.all([
      api.get(bomId), api.transitions(bomId), api.approval(bomId),
    ])
    setBom(b); setTransitions(t.transitions); setApproval(a)
    setLoading(false)
  }, [bomId])

  useEffect(() => { load() }, [load])

  async function handleApprove(e) {
    e.preventDefault(); setApproving(true); setError('')
    try { await api.approve(bomId, { approver_id: approver, notes: '' }); await load(); setApprover('') }
    catch (err) { setError(err.message) }
    finally { setApproving(false) }
  }

  async function handleTransition(to_status) {
    setTransitioning(true); setError('')
    try { await api.transition(bomId, { to_status, actor: 'ui-user', notes: '' }); await load(); onUpdated?.() }
    catch (err) { setError(err.message) }
    finally { setTransitioning(false) }
  }

  const NEXT = { DRAFT: ['REVIEW','REJECTED'], REVIEW: ['RELEASED','REJECTED'], RELEASED: ['ARCHIVED','REJECTED'], REJECTED: ['DRAFT'] }

  if (loading) return <Modal title="BOM detail" onClose={onClose} width="max-w-3xl"><PageSpinner /></Modal>

  return (
    <Modal title={bom.name} onClose={onClose} width="max-w-3xl">
      <div className="flex items-center gap-3 mb-4">
        <MonoId value={bom.id} />
        <span className="font-mono text-xs text-slate-400">v{bom.version}</span>
        <StatusBadge value={bom.status} />
      </div>

      <div className="flex gap-4 border-b border-slate-200 mb-4">
        {['components', 'approval', 'history', 'ai review'].map(t => (
          <button key={t} onClick={() => setTab(t)}
            className={`pb-2 text-xs font-medium capitalize border-b-2 transition-colors
              ${tab === t ? 'border-slate-800 text-slate-800' : 'border-transparent text-slate-400 hover:text-slate-600'}`}>
            {t}
          </button>
        ))}
      </div>

      <ErrorBanner message={error} onDismiss={() => setError('')} />

      {tab === 'components' && (
        bom.components?.length === 0
          ? <EmptyState message="No components" />
          : <table className="data-table">
              <thead><tr><th>Part ID</th><th>Name</th><th>Criticality</th><th>Qty</th><th>Ref</th></tr></thead>
              <tbody>
                {bom.components?.map(c => (
                  <tr key={c.component_id}>
                    <td><MonoId value={c.part_id} /></td>
                    <td className="text-sm">{c.part_name}</td>
                    <td><CriticalityBadge value={c.criticality} /></td>
                    <td className="text-sm">{c.quantity} {c.unit_of_measure}</td>
                    <td className="font-mono text-xs text-slate-400">{c.reference_designator || '—'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
      )}

      {tab === 'approval' && (
        <div className="space-y-4">
          {/* Current approval */}
          {approval ? (
            <div className="bg-green-50 border border-green-200 rounded p-3 text-sm">
              <p className="font-medium text-green-700 mb-1">Approved</p>
              <p className="text-green-600">by <span className="font-mono">{approval.approver_id}</span></p>
              {approval.notes && <p className="text-green-500 text-xs mt-1">{approval.notes}</p>}
            </div>
          ) : (
            <div className="bg-slate-50 border border-slate-200 rounded p-3">
              <p className="text-sm text-slate-500 mb-3">No approval on record</p>
              <form onSubmit={handleApprove} className="flex gap-2">
                <input className="field flex-1 text-xs" value={approver}
                  onChange={e => setApprover(e.target.value)}
                  placeholder="Your email / ID" required />
                <button type="submit" className="btn-primary text-xs" disabled={approving}>
                  {approving ? <Spinner size={12} /> : <><CheckCircle size={12} />Approve</>}
                </button>
              </form>
            </div>
          )}

          {/* State machine transitions */}
          <div>
            <p className="text-xs font-medium text-slate-400 uppercase tracking-wider mb-2">Advance status</p>
            <div className="flex gap-2 flex-wrap">
              {(NEXT[bom.status] || []).map(s => (
                <button key={s} className="btn-secondary text-xs"
                  onClick={() => handleTransition(s)} disabled={transitioning}>
                  {transitioning ? <Spinner size={12} /> : null}
                  → {s}
                </button>
              ))}
              {!(NEXT[bom.status]?.length) && (
                <span className="text-xs text-slate-400">No transitions available</span>
              )}
            </div>
          </div>
        </div>
      )}

      {tab === 'ai review' && (
        <AIReview
          title="AI BOM Review"
          buttonLabel="Generate Pre-Approval Review"
          onGenerate={() => api.aiReview(bomId)}
        />
      )}

      {tab === 'history' && (
        transitions.length === 0
          ? <EmptyState message="No transitions recorded yet" />
          : <div className="space-y-0">
              {transitions.map((t, i) => (
                <div key={t.transition_id} className="flex items-start gap-3 py-2.5 border-b border-slate-100 last:border-0">
                  <div className="w-1.5 h-1.5 rounded-full bg-slate-300 mt-1.5 shrink-0" />
                  <div className="flex-1">
                    <div className="flex items-center gap-2 text-sm">
                      <StatusBadge value={t.from_status} />
                      <ArrowRight size={12} className="text-slate-300" />
                      <StatusBadge value={t.to_status} />
                      <span className="text-xs text-slate-400 ml-auto font-mono">{t.actor}</span>
                    </div>
                    <div className="text-xs text-slate-400 mt-0.5 font-mono">{t.timestamp}</div>
                  </div>
                </div>
              ))}
            </div>
      )}
    </Modal>
  )
}

// ── BOMs page ─────────────────────────────────────────────────────────────────

export default function BOMs() {
  const [data,       setData]       = useState([])
  const [loading,    setLoading]    = useState(true)
  const [error,      setError]      = useState('')
  const [statusFilter, setStatusFilter] = useState('')
  const [showCreate, setShowCreate] = useState(false)
  const [cloning,    setCloning]    = useState(null)
  const [diffing,    setDiffing]    = useState(null)
  const [selected,   setSelected]   = useState(null)

  const load = useCallback(async () => {
    setLoading(true); setError('')
    try {
      const params = statusFilter ? { status: statusFilter } : {}
      setData(await api.list(params))
    } catch (err) { setError(err.message) }
    finally { setLoading(false) }
  }, [statusFilter])

  useEffect(() => { load() }, [load])

  return (
    <div className="p-6">
      <SectionHeader
        title={`BOMs ${data.length ? `(${data.length})` : ''}`}
        action={
          <div className="flex items-center gap-2">
            <select className="field text-xs py-1.5 w-36" value={statusFilter} onChange={e => setStatusFilter(e.target.value)}>
              <option value="">All statuses</option>
              {['DRAFT','REVIEW','RELEASED','ARCHIVED','REJECTED'].map(s => <option key={s}>{s}</option>)}
            </select>
            <button className="btn-secondary" onClick={load}><RefreshCw size={13} /></button>
            <button className="btn-primary" onClick={() => setShowCreate(true)}>
              <Plus size={13} /> Create BOM
            </button>
          </div>
        }
      />

      <ErrorBanner message={error} onDismiss={() => setError('')} />

      <div className="panel">
        {loading ? <PageSpinner /> : data.length === 0 ? (
          <EmptyState message="No BOMs found"
            action={<button className="btn-primary" onClick={() => setShowCreate(true)}><Plus size={13} />Create first BOM</button>} />
        ) : (
          <table className="data-table">
            <thead><tr>
              <th>ID</th><th>Name</th><th>Version</th><th>Status</th><th>Components</th><th>Actions</th>
            </tr></thead>
            <tbody>
              {data.map(b => (
                <tr key={b.id}>
                  <td><MonoId value={b.id} /></td>
                  <td className="font-medium text-slate-800 cursor-pointer hover:text-blue-600"
                    onClick={() => setSelected(b.id)}>{b.name}</td>
                  <td className="font-mono text-xs text-slate-500">{b.version}</td>
                  <td><StatusBadge value={b.status} /></td>
                  <td className="text-slate-500 text-xs">{b.component_count ?? '—'}</td>
                  <td>
                    <div className="flex items-center gap-2">
                      <button onClick={() => setCloning(b.id)}
                        className="text-xs text-slate-500 hover:text-slate-800 flex items-center gap-1">
                        <GitBranch size={11} /> Clone
                      </button>
                      <button onClick={() => setDiffing(b.id)}
                        className="text-xs text-slate-500 hover:text-slate-800 flex items-center gap-1">
                        <GitCompare size={11} /> Diff
                      </button>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>

      {showCreate && <CreateBOMModal onClose={() => setShowCreate(false)} onCreated={load} />}
      {cloning    && <CloneBOMModal sourceBomId={cloning} onClose={() => setCloning(null)} onCreated={load} />}
      {diffing    && <DiffModal bomId={diffing} onClose={() => setDiffing(null)} />}
      {selected   && <BOMDetail bomId={selected} onClose={() => setSelected(null)} onUpdated={load} />}
    </div>
  )
}