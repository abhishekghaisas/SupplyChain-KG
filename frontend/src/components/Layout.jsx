import { NavLink } from 'react-router-dom'
import { useState, useEffect } from 'react'
import {
  Package, Truck, ClipboardList, Zap, Brain, Sparkles, MessageSquare,
  LogOut, Clock, AlertTriangle,
} from 'lucide-react'
import { logout, getTokenExpiry, isAuthenticated } from '../api/client'

const NAV = [
  { to: '/parts',     icon: Package,       label: 'Parts' },
  { to: '/suppliers', icon: Truck,         label: 'Suppliers' },
  { to: '/boms',      icon: ClipboardList, label: 'BOMs' },
  { to: '/disruption',icon: Zap,           label: 'Disruption' },
  { to: '/reasoning',  icon: Brain,     label: 'Reasoning' },
  { to: '/extraction', icon: Sparkles,      label: 'Extract' },
  { to: '/query',      icon: MessageSquare, label: 'Ask Graph' },
]

// ── Token expiry countdown — the signature element ────────────────────────────

function TokenTimer() {
  const [secondsLeft, setSecondsLeft] = useState(null)

  useEffect(() => {
    function update() {
      const expiry = getTokenExpiry()
      if (!expiry) { setSecondsLeft(null); return }
      setSecondsLeft(Math.max(0, Math.floor((expiry - Date.now()) / 1000)))
    }
    update()
    const id = setInterval(update, 1000)
    return () => clearInterval(id)
  }, [])

  if (secondsLeft === null) return null

  const mins = Math.floor(secondsLeft / 60)
  const secs = String(secondsLeft % 60).padStart(2, '0')
  const urgent  = secondsLeft < 120
  const warning = secondsLeft < 600

  return (
    <div className={`flex items-center gap-1.5 font-mono text-xs px-2 py-1 rounded
      ${urgent  ? 'bg-red-900/30 text-red-400' :
        warning ? 'bg-amber-900/30 text-amber-400' :
                  'bg-slate-800 text-slate-400'}`}>
      {urgent && <AlertTriangle size={11} />}
      {!urgent && <Clock size={11} />}
      {mins}:{secs}
    </div>
  )
}

// ── Layout ────────────────────────────────────────────────────────────────────

export default function Layout({ children, onLogout }) {
  async function handleLogout() {
    await logout()
    onLogout()
  }

  return (
    <div className="flex h-screen overflow-hidden">
      {/* Sidebar */}
      <aside className="w-56 shrink-0 bg-sidebar flex flex-col">
        {/* Logo */}
        <div className="px-5 py-5 border-b border-slate-800">
          <div className="text-white font-semibold text-sm tracking-tight">Supply Chain</div>
          <div className="text-slate-500 font-mono text-xs mt-0.5">Knowledge Graph</div>
        </div>

        {/* Nav */}
        <nav className="flex-1 py-4 px-3 space-y-0.5">
          {NAV.map(({ to, icon: Icon, label }) => (
            <NavLink
              key={to}
              to={to}
              className={({ isActive }) =>
                `flex items-center gap-2.5 px-3 py-2 rounded text-sm transition-colors
                 ${isActive
                   ? 'bg-white/10 text-white'
                   : 'text-slate-400 hover:text-slate-200 hover:bg-white/5'}`
              }
            >
              <Icon size={15} />
              {label}
            </NavLink>
          ))}
        </nav>

        {/* Bottom */}
        <div className="px-3 py-4 border-t border-slate-800 space-y-2">
          <TokenTimer />
          <button
            onClick={handleLogout}
            className="flex items-center gap-2 w-full px-3 py-2 text-xs text-slate-500
                       hover:text-slate-300 transition-colors rounded hover:bg-white/5"
          >
            <LogOut size={13} />
            Sign out
          </button>
        </div>
      </aside>

      {/* Main */}
      <main className="flex-1 overflow-y-auto bg-canvas">
        {children}
      </main>
    </div>
  )
}