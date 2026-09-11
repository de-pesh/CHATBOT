import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import './index.css'
import App from './App.tsx'

// Standalone-dev-only: in production this is supplied as a prop by the
// Module Federation host shell. Points at the local chatbot-inferencing-service.
const API_ROUTES = {
  Chatbot: {
    sendChatMessage: 'http://localhost:8003/chat',
    getChatHistory: 'http://localhost:8003/chat/history',
  },
}

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <App API_ROUTES={API_ROUTES} />
  </StrictMode>,
)
