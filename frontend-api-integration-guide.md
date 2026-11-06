# Frontend API Integration Guide — Chatbot

## Base URL
All endpoints are prefixed with `/chat`. Confirm the host with the backend team.

---

## How Identity Works (Important — Read First)

The chatbot does **not** have its own login. It is embedded inside the platform via Module Federation.

User identity (who owns a session, who sent a message) comes entirely from the platform. The platform's **reverse proxy** injects the user's identity as request headers before forwarding to the chatbot API. The chatbot reads those headers and links every session and chat history record to that user.

```
Platform user is already logged in
        │
        ▼
Frontend calls chatbot API (through the platform proxy, not directly)
        │
        ▼
Proxy automatically injects these headers on every request:
  X-Entity-Id:      <entity_id from platform>
  X-Tenant-Id:      <tenant_id from platform>
  X-User-Id:        <user_id from platform>
  X-User-Type:      <user_type e.g. "standard_user">
  X-Internal-Token: <shared secret — backend team provides this>
        │
        ▼
Chatbot links all sessions and history to that X-User-Id
```

**The frontend must send these 4 headers on every request.** Use an axios interceptor — configure it once after login and every call carries the headers automatically.

```js
import axios from 'axios'

const chatApi = axios.create({
  baseURL: 'http://your-chatbot-url'
})

// Set up once after the platform login gives you the current user's IDs
chatApi.interceptors.request.use((config) => {
  config.headers['x-entity-id'] = currentUser.entity_id
  config.headers['x-tenant-id'] = currentUser.tenant_id
  config.headers['x-user-id']   = currentUser.user_id
  config.headers['x-user-type'] = currentUser.user_type
  return config
})

// Every call now carries the headers automatically — no need to add them manually
chatApi.post('/chat/stream', { chat: [{ role: 'user', content: 'Hello' }] })
```

`currentUser.entity_id`, `tenant_id`, `user_id`, `user_type` come from the platform's own login response — ask the platform team which field names they use.

### Where to get the IDs

| Header | Where it comes from |
|--------|-------------------|
| `X-Entity-Id` | Entity GUID from the platform's login response |
| `X-Tenant-Id` | Tenant GUID from the platform's login response |
| `X-User-Id` | Logged-in user's GUID from the platform ← **most critical**, isolates each user's sessions |
| `X-User-Type` | User type string (e.g. `"standard_user"`) |

### Testing from Swagger

Swagger has no built-in input for these headers (they are not declared as endpoint parameters). Use the browser console to set them once — they persist across page refreshes via `localStorage`.

**Step 1 — Open Swagger in the browser, press F12, go to Console tab**

**Step 2 — Paste and run this (replace values with real IDs, or leave as demo):**
```js
localStorage.setItem('sw_entity_id', '11111111-1111-1111-1111-111111111111')
localStorage.setItem('sw_tenant_id', '22222222-2222-2222-2222-222222222222')
localStorage.setItem('sw_user_id',   '33333333-3333-3333-3333-333333333333')
localStorage.setItem('sw_user_type', 'demo_user')
```

**Step 3 — Refresh the Swagger page**

Done. Every request from Swagger will now carry those headers automatically. All sessions and history will be saved under the `user_id` you set.

To switch users, just run the same commands with different IDs and refresh.

### Testing from Postman / curl

Add these as request headers on every call:
```
X-Entity-Id: 11111111-1111-1111-1111-111111111111
X-Tenant-Id: 22222222-2222-2222-2222-222222222222
X-User-Id:   33333333-3333-3333-3333-333333333333
X-User-Type: demo_user
```

---

## Core Flow

```
1. Platform user opens the chatbot widget
        │
        ▼
2. Frontend calls POST /chat/stream — no conversation_id on first message
        │  (proxy injects X-Entity-Id, X-Tenant-Id, X-User-Id automatically)
        ▼
3. First SSE event: meta → { "conversation_id": "abc-123" }
        │
        ▼
4. Store conversation_id in component state
        │
        ▼
5. Stream delta events → render answer progressively
        │
        ▼
6. Final SSE event: done → full answer + suggested follow-up questions
        │
        ▼
7. User sends next message → pass conversation_id back in payload
        │
        └── repeat from step 2
```

---

## 1. Start a New Conversation

**`POST /chat/stream`**

```json
{
  "chat": [
    { "role": "user", "content": "What is the refund policy?" }
  ],
  "local_hour": 14
}
```

- `conversation_id` — omit on the first message; backend creates the session and returns it
- `local_hour` — optional, the user's current hour (0–23), helps time-aware answers
- `chat` — always a list; for a new conversation just one `user` message

---

## 2. Continue a Conversation

Pass the `conversation_id` received from step 1:

```json
{
  "conversation_id": "abc-123-guid",
  "chat": [
    { "role": "user", "content": "What about digital products?" }
  ]
}
```

The backend fetches prior history from the DB automatically — you do **not** need to send the full history yourself.

---

## 3. Handling the SSE Stream

The streaming endpoint returns `text/event-stream`. Parse events in order:

| Event | Payload | What to do |
|-------|---------|------------|
| `meta` | `{ "conversation_id": "..." }` | Save this — use it on all future turns |
| `stage` | `{ "stage": "retrieving" }` | Optional: show a progress indicator |
| `delta` | `{ "text": "chunk..." }` | Append to the displayed answer |
| `done` | See below | Replace streamed text with final answer |
| `error` | `{ "detail": "...", "conversation_id": "..." }` | Show error to user |

**`done` payload:**
```json
{
  "response": "<final answer with citation links>",
  "conversation_id": "abc-123",
  "history_id": "xyz-456",
  "collections": ["kb_docs"],
  "suggested_questions": ["Tell me more", "What about X?"],
  "timings": { "pipeline_ms": 1200 }
}
```

> **Important:** Use `response` from `done` as the authoritative answer — it replaces the concatenated `delta` chunks (citations are linkified and non-English answers are translated after generation).

---

## 4. Suggested Follow-up Questions

The `done` event includes `suggested_questions` — a list of strings. Render these as clickable chips below the answer. When clicked, send them as the next `user` message with the same `conversation_id`.

---

## 5. Non-Streaming Alternative

If SSE is not suitable, use `POST /chat` — same payload, blocks until the full answer is ready:

```json
{
  "response": "...",
  "conversation_id": "abc-123",
  "history_id": "xyz-456",
  "suggested_questions": ["..."],
  "timings": {}
}
```

---

## 6. List Past Sessions (Conversation History Sidebar)

**`GET /chat/session`**

```
GET /chat/session?is_active=true&page_number=1&page_size=20
```

Returns sessions belonging to the calling user (identified via `X-User-Id`). Use `id` from each record as `conversation_id` to resume a session.

---

## 7. Load Messages for a Session

**`GET /chat/history`**

```
GET /chat/history?conversation_id=abc-123&page_number=1&page_size=50
```

Returns paginated `{ prompt, response }` records for displaying prior turns when the user reopens a session.

---

## 8. Rename or Delete a Session

**Rename:** `PATCH /chat/session/{session_id}`
```json
{ "name": "Refund policy questions" }
```

**Delete:** `DELETE /chat/session/{session_id}` — soft delete, no body needed.

A user can only rename/delete their own sessions (ownership is enforced by `X-User-Id`).

---

## 9. Rate an Answer (Thumbs Up/Down)

**`POST /chat/history/{history_id}/feedback`**

`history_id` comes from the `done` SSE event.

```json
{ "rating": "positive" }
```
```json
{ "rating": "negative", "comment": "Answer was incomplete" }
```

---

## Summary of Endpoints

| Method | Path | Purpose |
|--------|------|---------|
| `POST` | `/chat/stream` | Send a message, stream the answer (preferred) |
| `POST` | `/chat` | Send a message, wait for full answer |
| `GET` | `/chat/session` | List the calling user's past sessions |
| `PATCH` | `/chat/session/{id}` | Rename a session |
| `DELETE` | `/chat/session/{id}` | Delete a session |
| `GET` | `/chat/history` | Fetch messages in a session |
| `POST` | `/chat/history/{id}/feedback` | Rate an answer |
