import { Routes, Route, Navigate } from 'react-router-dom'
import { useAuthStore } from '@/store/authStore'
import AuthPage from '@/pages/AuthPage'
import ChatPage from '@/pages/ChatPage'
import TitleBar from '@/components/layout/TitleBar'

function App() {
  const isAuthed = useAuthStore(s => s.isAuthed)

  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100vh', width: '100vw', overflow: 'hidden' }}>
      <TitleBar />
      <div style={{ flex: 1, overflow: 'hidden' }}>
        <Routes>
          <Route path="/auth" element={isAuthed ? <Navigate to="/" replace /> : <AuthPage />} />
          <Route path="/*"   element={isAuthed ? <ChatPage />  : <Navigate to="/auth" replace />} />
        </Routes>
      </div>
    </div>
  )
}

export default App
