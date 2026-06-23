import { useState } from 'react'
import { login, ApiError } from '../api/client'
import { Spinner } from '../components/ui'

export default function Login({ onSuccess }) {
  const [clientId,     setClientId]     = useState('supply-chain-api')
  const [clientSecret, setClientSecret] = useState('')
  const [error,  setError]  = useState('')
  const [loading, setLoading] = useState(false)

  async function handleSubmit(e) {
    e.preventDefault()
    setError('')
    setLoading(true)
    try {
      await login(clientId, clientSecret)
      onSuccess()
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Login failed')
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="min-h-screen bg-sidebar flex items-center justify-center p-4">
      <div className="w-full max-w-sm">
        {/* Header */}
        <div className="mb-8">
          <div className="text-white font-semibold text-xl tracking-tight mb-1">
            Supply Chain KG
          </div>
          <div className="text-slate-500 font-mono text-xs">
            Knowledge Graph Interface
          </div>
        </div>

        {/* Form */}
        <form onSubmit={handleSubmit} className="space-y-4">
          <div>
            <label className="block text-xs font-medium text-slate-400 mb-1.5">
              Client ID
            </label>
            <input
              type="text"
              value={clientId}
              onChange={e => setClientId(e.target.value)}
              className="w-full px-3 py-2.5 bg-slate-800 border border-slate-700
                         text-white text-sm rounded focus:outline-none
                         focus:border-slate-500 font-mono placeholder-slate-600"
              placeholder="supply-chain-api"
              required
            />
          </div>

          <div>
            <label className="block text-xs font-medium text-slate-400 mb-1.5">
              Client Secret
            </label>
            <input
              type="password"
              value={clientSecret}
              onChange={e => setClientSecret(e.target.value)}
              className="w-full px-3 py-2.5 bg-slate-800 border border-slate-700
                         text-white text-sm rounded focus:outline-none
                         focus:border-slate-500 placeholder-slate-600"
              placeholder="••••••••••••"
              required
            />
          </div>

          {error && (
            <p className="text-xs text-red-400 bg-red-900/20 border border-red-900/40
                          px-3 py-2 rounded">
              {error}
            </p>
          )}

          <button
            type="submit"
            disabled={loading}
            className="w-full flex items-center justify-center gap-2 py-2.5
                       bg-white text-slate-900 text-sm font-medium rounded
                       hover:bg-slate-100 transition-colors disabled:opacity-50"
          >
            {loading && <Spinner size={14} />}
            {loading ? 'Signing in…' : 'Sign in'}
          </button>
        </form>
      </div>
    </div>
  )
}