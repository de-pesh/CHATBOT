import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import './index.css'
import App from './App.tsx'

// Standalone-dev-only: in production this is supplied as a prop by the
// Module Federation host shell. Points at main-service, which serves /chat
// since the chat service was merged into it (it was on :8003 before).
const API_ROUTES = {
  Chatbot: {
    sendChatMessage: 'http://localhost:8000/chat',
    getChatHistory: 'http://localhost:8000/chat/history',
  },
}

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <App API_ROUTES={API_ROUTES} />
  </StrictMode>,
)
