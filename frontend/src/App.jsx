import { useState, useEffect } from 'react'
import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom'
import { setAuthChangeCallback, isAuthenticated } from './api/client'
import Layout  from './components/Layout'
import Login   from './pages/Login'
import Parts   from './pages/Parts'
import Suppliers from './pages/Suppliers'
import BOMs    from './pages/BOMs'
import Disruption from './pages/Disruption'
import Reasoning  from './pages/Reasoning'
import Extraction from './pages/Extraction'
import Query      from './pages/Query'

export default function App() {
  const [authed, setAuthed] = useState(isAuthenticated())

  useEffect(() => {
    setAuthChangeCallback(setAuthed)
  }, [])

  if (!authed) {
    return <Login onSuccess={() => setAuthed(true)} />
  }

  return (
    <BrowserRouter>
      <Layout onLogout={() => setAuthed(false)}>
        <Routes>
          <Route path="/"           element={<Navigate to="/parts" replace />} />
          <Route path="/parts"      element={<Parts />} />
          <Route path="/suppliers"  element={<Suppliers />} />
          <Route path="/boms"       element={<BOMs />} />
          <Route path="/disruption" element={<Disruption />} />
          <Route path="/reasoning"  element={<Reasoning />} />
          <Route path="/query"      element={<Query />} />
          <Route path="/extraction" element={<Extraction />} />
        </Routes>
      </Layout>
    </BrowserRouter>
  )
}