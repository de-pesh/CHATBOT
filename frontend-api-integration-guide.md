# Frontend API Integration Guide — Chatbot

## Base URL
All endpoints are prefixed with `/chat`. Confirm the host with the backend team.

---

## Authentication

Every request needs a `Bearer` token in the `Authorization` header:

```
Authorization: Bearer <jwt_token>
```

Obtain the JWT from your auth/login flow before hitting any chat endpoint.

---

## Core Flow

```
1. User types a message
        │
        ▼
2. POST /chat/stream  (first message → no conversation_id)
        │
        ▼
3. Backend returns  event: meta  →  { conversation_id: "abc-123" }
        │
        ▼
4. Store conversation_id in component state
        │
        ▼
5. Stream delta events → render answer progressively
        │
        ▼
6. event: done  →  final answer + suggested follow-up questions
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

- `conversation_id` — omit on the first message; backend creates the session
- `local_hour` — optional, the user's current hour (0–23), helps time-aware answers
- `chat` — always a list; for a new conversation just one `user` message

---

## 2. Continue a Conversation

Pass the `conversation_id` you received in step 1:

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

If you don't want SSE, use `POST /chat` — same payload, blocks until the full answer is ready, returns a single JSON response:

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

Returns the user's sessions. Use `id` from each record as `conversation_id` to resume a session.

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

---

## 9. Rate an Answer (Thumbs Up/Down)

**`POST /chat/history/{history_id}/feedback`**

`history_id` comes from the `done` event.

```json
{
  "rating": "positive"
}
```
or
```json
{
  "rating": "negative",
  "comment": "Answer was incomplete"
}
```

---

## Summary of Endpoints

| Method | Path | Purpose |
|--------|------|---------|
| `POST` | `/chat/stream` | Send a message, stream the answer (preferred) |
| `POST` | `/chat` | Send a message, wait for full answer |
| `GET` | `/chat/session` | List the user's past sessions |
| `PATCH` | `/chat/session/{id}` | Rename a session |
| `DELETE` | `/chat/session/{id}` | Delete a session |
| `GET` | `/chat/history` | Fetch messages in a session |
| `POST` | `/chat/history/{id}/feedback` | Rate an answer |
