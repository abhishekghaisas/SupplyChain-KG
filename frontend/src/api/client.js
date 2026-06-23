/**
 * API client for the Supply Chain Knowledge Graph API.
 *
 * - All requests go through `request()` which attaches the Bearer token.
 * - On 401, attempts a single token refresh then retries.
 * - Tokens are stored in module-level memory (never localStorage).
 */

const BASE = '/api'

// ── Token store (in-memory only) ──────────────────────────────────────────────

let _accessToken   = null
let _refreshToken  = null
let _expiresAt     = null   // Date when access token expires
let _onAuthChange  = null   // callback(isAuthenticated)

export function setAuthChangeCallback(fn) { _onAuthChange = fn }

export function getTokenExpiry() { return _expiresAt }

export function isAuthenticated() { return !!_accessToken && _expiresAt > new Date() }

function _setTokens({ access_token, refresh_token, expires_in }) {
  _accessToken  = access_token
  _refreshToken = refresh_token
  _expiresAt    = new Date(Date.now() + expires_in * 1000)
  _onAuthChange?.(true)
}

function _clearTokens() {
  _accessToken  = null
  _refreshToken = null
  _expiresAt    = null
  _onAuthChange?.(false)
}

// ── Core request ──────────────────────────────────────────────────────────────

export async function request(path, options = {}, retry = true) {
  const headers = {
    'Content-Type': 'application/json',
    ...options.headers,
  }
  if (_accessToken) headers['Authorization'] = `Bearer ${_accessToken}`

  const res = await fetch(`${BASE}${path}`, { ...options, headers })

  // Auto-refresh on 401 (once)
  if (res.status === 401 && retry && _refreshToken) {
    const refreshed = await _doRefresh()
    if (refreshed) return request(path, options, false)
    _clearTokens()
    throw new AuthError('Session expired — please log in again')
  }

  if (!res.ok) {
    const body = await res.json().catch(() => ({}))
    throw new ApiError(res.status, body.detail || 'Request failed', body)
  }

  // 204 No Content
  if (res.status === 204) return null

  return res.json()
}

async function _doRefresh() {
  try {
    const params = new URLSearchParams({
      grant_type:    'refresh_token',
      refresh_token: _refreshToken,
    })
    const res = await fetch(`${BASE}/auth/refresh`, {
      method:  'POST',
      headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
      body:    params.toString(),
    })
    if (!res.ok) return false
    _setTokens(await res.json())
    return true
  } catch {
    return false
  }
}

// ── Error types ───────────────────────────────────────────────────────────────

export class ApiError extends Error {
  constructor(status, message, body = {}) {
    super(message)
    this.status = status
    this.body   = body
  }
}

export class AuthError extends Error {}

// ── Auth endpoints ────────────────────────────────────────────────────────────

export async function login(clientId, clientSecret) {
  const params = new URLSearchParams({
    grant_type:    'client_credentials',
    client_id:     clientId,
    client_secret: clientSecret,
  })
  const res = await fetch(`${BASE}/auth/token`, {
    method:  'POST',
    headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
    body:    params.toString(),
  })
  if (!res.ok) {
    const body = await res.json().catch(() => ({}))
    throw new ApiError(res.status, body.detail || 'Login failed')
  }
  _setTokens(await res.json())
}

export async function logout() {
  if (_refreshToken) {
    await fetch(`${BASE}/auth/revoke`, {
      method:  'POST',
      headers: { 'Content-Type': 'application/json' },
      body:    JSON.stringify({ refresh_token: _refreshToken }),
    }).catch(() => {})
  }
  _clearTokens()
}

// ── Parts ─────────────────────────────────────────────────────────────────────

export const parts = {
  list:   (params = {}) => request(`/parts?${new URLSearchParams(params)}`),
  search: (query)        => request(`/parts?${new URLSearchParams({ id_prefix: query })}`),
  get:    id            => request(`/parts/${id}`),
  create: body          => request('/parts', { method: 'POST', body: JSON.stringify(body) }),
  suppliers:     id     => request(`/parts/${id}/suppliers`),
  compatibility: id     => request(`/parts/${id}/compatibility`),
  boms:          id     => request(`/parts/${id}/boms`),
  suggestSubstitutes: (id, max=5) => request(`/parts/${id}/suggest-substitutes?max_candidates=${max}`, { method: 'POST' }),
  persistSubstitutes: (id, body)  => request(`/parts/${id}/persist-substitutes`, { method: 'POST', body: JSON.stringify(body) }),
}

// ── Suppliers ─────────────────────────────────────────────────────────────────

export const suppliers = {
  list:       ()   => request('/suppliers'),
  get:        id   => request(`/suppliers/${id}`),
  create:     body => request('/suppliers', { method: 'POST', body: JSON.stringify(body) }),
  disruption: id   => request(`/suppliers/${id}/disruption`),
  aiQualify:  id   => request(`/suppliers/${id}/ai-qualify`, { method: 'POST' }),
}

// ── BOMs ──────────────────────────────────────────────────────────────────────

export const boms = {
  list:       (params = {}) => request(`/boms?${new URLSearchParams(params)}`),
  get:        id            => request(`/boms/${id}`),
  create:     body          => request('/boms', { method: 'POST', body: JSON.stringify(body) }),
  delete:     id            => request(`/boms/${id}`, { method: 'DELETE' }),
  addComponent: (id, body)  => request(`/boms/${id}/components`, { method: 'POST', body: JSON.stringify(body) }),
  risk:       id            => request(`/boms/${id}/risk`),
  clone:      (id, body)    => request(`/boms/${id}/clone`, { method: 'POST', body: JSON.stringify(body) }),
  diff:       (a, b)        => request(`/boms/${a}/diff/${b}`),
  lineage:    id            => request(`/boms/${id}/lineage`),
  approve:    (id, body)    => request(`/boms/${id}/approve`, { method: 'POST', body: JSON.stringify(body) }),
  transition: (id, body)    => request(`/boms/${id}/transition`, { method: 'POST', body: JSON.stringify(body) }),
  transitions: id           => request(`/boms/${id}/transitions`),
  approval:   id            => request(`/boms/${id}/approval`),
  aiReview:   id            => request(`/boms/${id}/ai-review`, { method: 'POST' }),
}

// ── Disruption ────────────────────────────────────────────────────────────────

export const disruption = {
  aiNarrate: (body) => request('/disruption/ai-narrate', { method: 'POST', body: JSON.stringify(body) }),
  supplier: (id, statuses = 'RELEASED') =>
    request(`/disruption/supplier/${id}?statuses=${statuses}`),
  part: (id, statuses = 'RELEASED') =>
    request(`/disruption/part/${id}?statuses=${statuses}`),
}

// ── Reasoning ─────────────────────────────────────────────────────────────────

export const reasoning = {
  compatibility: body => request('/reasoning/compatibility', { method: 'POST', body: JSON.stringify(body) }),
  leadTime:      body => request('/reasoning/lead-time',     { method: 'POST', body: JSON.stringify(body) }),
  qualifySupplier: body => request('/reasoning/qualify-supplier', { method: 'POST', body: JSON.stringify(body) }),
}