import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import './index.css'
import App from './App.tsx'

// Standalone-dev-only: in production this is supplied as a prop by the
// Module Federation host shell. Points at main-service, which serves /chat
// since the chat service was merged into it (it was on :8003 before).
// Where main-service runs. Set VITE_API_BASE_URL in the repo-root .env to point
// at another host or port (Docker: it is passed to the image build; see
// docker-compose.yml); the default is a local main-service.
const API_BASE = (import.meta.env.VITE_API_BASE_URL || 'http://localhost:8000').replace(/\/+$/, '')
const API_ROUTES = {
  Chatbot: {
    sendChatMessage: `${API_BASE}/chat`,
    streamChatMessage: `${API_BASE}/chat/stream`,
    getChatHistory: `${API_BASE}/chat/history`,
    getChatSession: `${API_BASE}/chat/session`,
    // Only reached if DISABLE_AUTH=False on the backend - see Chatbot.tsx's AuthGate.
    authenticate: `${API_BASE}/auth/authenticate`,
    me: `${API_BASE}/chat/me`,
  },
}

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <App API_ROUTES={API_ROUTES} />
  </StrictMode>,
)
