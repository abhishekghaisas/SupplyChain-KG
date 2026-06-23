/**
 * Shared UI primitives: Badge, Spinner, EmptyState, ErrorBanner, Modal, Confirm.
 */

import { X, AlertCircle, Loader2 } from 'lucide-react'

// ── Badge ─────────────────────────────────────────────────────────────────────

const CRITICALITY_CLASS = {
  CRITICAL: 'badge-critical',
  HIGH:     'badge-high',
  MEDIUM:   'badge-medium',
  LOW:      'badge-low',
}

const STATUS_CLASS = {
  RELEASED: 'badge-released',
  DRAFT:    'badge-draft',
  REVIEW:   'badge-review',
  ARCHIVED: 'badge-archived',
  REJECTED: 'badge-rejected',
}

export function CriticalityBadge({ value }) {
  return <span className={`badge ${CRITICALITY_CLASS[value] ?? 'badge-low'}`}>{value}</span>
}

export function StatusBadge({ value }) {
  return <span className={`badge ${STATUS_CLASS[value] ?? 'badge-draft'}`}>{value}</span>
}

export function SeverityBadge({ label }) {
  return <span className={`badge ${CRITICALITY_CLASS[label] ?? 'badge-low'}`}>{label}</span>
}

// ── Spinner ───────────────────────────────────────────────────────────────────

export function Spinner({ size = 16 }) {
  return <Loader2 size={size} className="animate-spin text-slate-400" />
}

export function PageSpinner() {
  return (
    <div className="flex items-center justify-center h-64">
      <Spinner size={24} />
    </div>
  )
}

// ── Empty state ───────────────────────────────────────────────────────────────

export function EmptyState({ message = 'No data', action }) {
  return (
    <div className="flex flex-col items-center justify-center py-16 text-slate-400">
      <p className="text-sm">{message}</p>
      {action && <div className="mt-3">{action}</div>}
    </div>
  )
}

// ── Error banner ──────────────────────────────────────────────────────────────

export function ErrorBanner({ message, onDismiss }) {
  if (!message) return null
  return (
    <div className="flex items-start gap-2 px-4 py-3 bg-red-50 border border-red-200 rounded text-sm text-red-700 mb-4">
      <AlertCircle size={15} className="mt-0.5 shrink-0" />
      <span className="flex-1">{message}</span>
      {onDismiss && (
        <button onClick={onDismiss} className="text-red-400 hover:text-red-600">
          <X size={14} />
        </button>
      )}
    </div>
  )
}

// ── Modal ─────────────────────────────────────────────────────────────────────

export function Modal({ title, onClose, children, width = 'max-w-lg' }) {
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4">
      {/* Backdrop */}
      <div className="absolute inset-0 bg-black/40" onClick={onClose} />
      {/* Panel */}
      <div className={`relative bg-white border border-slate-200 rounded shadow-xl w-full ${width} max-h-[90vh] flex flex-col`}>
        <div className="flex items-center justify-between px-5 py-4 border-b border-slate-200">
          <h2 className="text-sm font-semibold text-slate-800">{title}</h2>
          <button onClick={onClose} className="text-slate-400 hover:text-slate-600">
            <X size={16} />
          </button>
        </div>
        <div className="overflow-y-auto p-5 flex-1">{children}</div>
      </div>
    </div>
  )
}

// ── Confirm dialog ────────────────────────────────────────────────────────────

export function Confirm({ message, onConfirm, onCancel }) {
  return (
    <Modal title="Confirm" onClose={onCancel} width="max-w-sm">
      <p className="text-sm text-slate-700 mb-5">{message}</p>
      <div className="flex justify-end gap-2">
        <button className="btn-secondary" onClick={onCancel}>Cancel</button>
        <button className="btn-danger" onClick={onConfirm}>Confirm</button>
      </div>
    </Modal>
  )
}

// ── Form field ────────────────────────────────────────────────────────────────

export function Field({ label, children, hint }) {
  return (
    <div className="mb-4">
      {label && <label className="field-label">{label}</label>}
      {children}
      {hint && <p className="mt-1 text-xs text-slate-400">{hint}</p>}
    </div>
  )
}

// ── Section header ────────────────────────────────────────────────────────────

export function SectionHeader({ title, action }) {
  return (
    <div className="flex items-center justify-between mb-4">
      <h2 className="text-sm font-semibold text-slate-700 uppercase tracking-wider">{title}</h2>
      {action}
    </div>
  )
}

// ── Monospace ID ──────────────────────────────────────────────────────────────

export function MonoId({ value }) {
  return <span className="font-mono text-xs text-slate-500">{value}</span>
}