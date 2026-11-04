# Chatbot API Reference

> **Base URL:** `/`  
> **Auth:** All endpoints require `Authorization: Bearer <token>` except `POST /auth/authenticate`.  
> **Content-Type:** `application/json` (unless multipart for file uploads)

---

## Table of Contents

- [Authentication](#authentication)
- [Chat](#chat)
- [Files](#files)
- [DMS](#dms-document-management)
- [Prompts](#prompt-management)
- [Admin](#admin)
- [Error Codes](#error-codes)

---

## Authentication

### POST `/auth/authenticate`
Authenticate a user and receive a JWT access token.

**Request** — `application/x-www-form-urlencoded`

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `username` | string | ✅ | User email address |
| `password` | string | ✅ | User password |

**Response 200**
```json
{
  "access_token": "eyJ...",
  "token_type": "bearer"
}
```

---

### POST `/auth/re-authenticate`
Refresh an existing JWT to extend the session.

No body required. Send the current Bearer token.

**Response 200** — same shape as `/auth/authenticate`

---

### POST `/auth/change-password`
Change the authenticated user's password.

| Field | Type | Required |
|-------|------|----------|
| `current_password` | string | ✅ |
| `new_password` | string | ✅ |

**Responses:** `200 OK` · `400 Bad Request` · `401 Unauthorized`

---

### POST `/auth/users` 🔒 Admin
Create a new user account.

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `email` | string | ✅ | New user's email |
| `password` | string | ✅ | Initial password |
| `role` | string | ✅ | `"admin"` or `"user"` |

**Responses:** `201 Created` · `403 Forbidden`

---

## Chat

### POST `/chat`
Send a chat message — **blocking** (waits for the full answer).  
Prefer `/chat/stream` for interactive UI.

**Request Body**

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `chat` | `Message[]` | ✅ | Full conversation history. Last item is the new user message. Each item: `{ "role": "user"/"assistant", "content": "..." }` |
| `conversation_id` | string\|int\|null | ❌ | Omit to start a new session; pass the returned value to continue one |
| `local_hour` | int (0–23) | ❌ | User's local hour for time-aware answers |

**Response 200**
```json
{
  "response": "The answer with inline [1] citation links...",
  "conversation_id": "abc-123",
  "history_id": "def-456",
  "suggested_questions": ["What is...?", "How does...?"],
  "timings": { "rephrase_ms": 120, "retrieve_ms": 340, "generate_ms": 1200 }
}
```

**Responses:** `200 OK` · `429 Rate Limited` · `500 Server Error`

---

### POST `/chat/stream`
Send a chat message — **streaming via Server-Sent Events**.

Same request body as `POST /chat`.  
Response is always HTTP 200; failures arrive as an `error` event inside the stream.

#### SSE Event Sequence

| Event | Payload | Description |
|-------|---------|-------------|
| `meta` | `{ conversation_id }` | Sent first — use this to track the session immediately |
| `stage` | `{ stage }` | Pipeline progress: `rephrasing` → `retrieving` → `reranking` → `generating` → `finalising` |
| `delta` | `{ text }` | Next chunk of the answer. Concatenate for a live preview. |
| `done` | `{ response, conversation_id, history_id, collections, timings, suggested_questions }` | **Final event. Use `response` as the authoritative answer** — replaces concatenated deltas (translated + citation links resolved). |
| `error` | `{ detail, conversation_id }` | Turn failed. May arrive after some `delta` events. Discard partial text and show the error. |

**Example (fetch + ReadableStream):**
```js
const res = await fetch('/chat/stream', {
  method: 'POST',
  headers: {
    'Authorization': 'Bearer ...',
    'Content-Type': 'application/json',
  },
  body: JSON.stringify({ chat: [{ role: 'user', content: 'Hello' }] }),
});
// Parse SSE frames from res.body.getReader()
```

---

### GET `/chat/me`
Get the current user's chat profile (display name + preferences).

**Response 200**
```json
{
  "id": 1,
  "email": "user@example.com",
  "display_name": "Alice",
  "preferences": { "role": "Compliance officer" }
}
```

**Responses:** `200 OK` · `503 Unavailable` (when auth is disabled)

---

### PATCH `/chat/me`
Update the current user's display name and/or preferences.  
Send at least one field.

| Field | Type | Description |
|-------|------|-------------|
| `display_name` | string\|null | How the assistant addresses the user. Empty string clears it. |
| `preferences` | object\|null | Key-value facts fed into prompts (e.g. `{"role":"Admin"}`). **Replaces all existing preferences.** Empty object clears them. |

**Responses:** `200 OK` · `422 Validation Error`

---

### GET `/chat/history`
Fetch the user's paginated chat history.

| Param | Type | Default | Description |
|-------|------|---------|-------------|
| `page_number` | int | 1 | |
| `page_size` | int | config default | Capped at max |
| `conversation_id` | string\|int | — | Filter to one session |
| `prompt` | string | — | Partial text match on the question |
| `is_active` | bool | — | Filter by active status |

**Response 200**
```json
{
  "total_records": 42,
  "page_number": 1,
  "page_size": 20,
  "data": [
    {
      "id": "...",
      "conversation_id": "...",
      "prompt": "...",
      "response": "...",
      "created_on": "2026-01-01T00:00:00",
      "grounding_verdict": "grounded"
    }
  ]
}
```

---

### GET `/chat/session`
Fetch the user's paginated chat sessions.

| Param | Type | Default | Description |
|-------|------|---------|-------------|
| `page_number` | int | 1 | |
| `page_size` | int | config default | |
| `id` | string\|int | — | Filter by session ID |
| `name` | string | — | Partial name match |
| `is_active` | bool | `true` | |

**Response 200**
```json
{
  "total_records": 5,
  "page_number": 1,
  "page_size": 20,
  "data": [{ "id": "...", "name": "...", "created_on": "..." }]
}
```

---

### PATCH `/chat/session/{session_id}`
Rename a session. Must be owned by the calling user.

| Field | Type | Required |
|-------|------|----------|
| `name` | string | ✅ |

**Responses:** `200 OK` · `404 Not Found`

---

### DELETE `/chat/session/{session_id}`
Soft-delete a session and its history. Must be owned by the calling user.

**Responses:** `204 No Content` · `404 Not Found`

---

### POST `/chat/history/{history_id}/feedback`
Submit thumbs up/down on an answer.  
`history_id` comes from the `done` SSE event or the blocking chat response.  
Submitting again for the same turn **replaces** the previous rating.

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `rating` | string | ✅ | `"positive"` or `"negative"` |
| `comment` | string\|null | ❌ | Optional detail, typically with a negative rating |

**Responses:** `200 OK` · `404 Not Found`

---

## Files

> All `/files` routes require **admin** role except `GET /files/download-by-name`.

---

### GET `/files/`  🔒 Admin
List all file records in the knowledge base.

**Response 200** — array of file objects:
```json
[{
  "id": 1,
  "name": "policy.pdf",
  "mimetype": "application/pdf",
  "processing_status": "completed",
  "is_active": true,
  "document_key": "uuid...",
  "version_number": 2,
  "version_label": "3.2"
}]
```

---

### POST `/files/`  🔒 Admin
Upload one or more files to the knowledge base.

**Request** — `multipart/form-data`

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `files` | File[] | ✅ | One or more files |
| `allow_duplicate` | bool | ❌ (default `false`) | Allow byte-identical re-uploads |
| `version_label` | string | ❌ | e.g. `"3.2"` — letters, digits, `.`, `_`, `-` |
| `effective_date` | date | ❌ | `YYYY-MM-DD` when this version takes effect |

> Parsing and indexing are **async**. The returned record's `processing_status` will be `"pending"` — poll `GET /files/{file_id}/status` to track progress.  
> A file whose name matches an existing document automatically becomes its next version.

**Responses:** `201 Created` · `409 Duplicate` · `413 Too Large` · `415 Unsupported Type`

---

### GET `/files/download-by-name`
Download a file by its original name. Available to all authenticated users.  
Used for citation download links in chat answers.

| Param | Type | Required | Description |
|-------|------|----------|-------------|
| `name` | string | ✅ | Original file name |
| `path` | string | ❌ | Stored blob path from a citation — skips the DB lookup |

**Responses:** `200 — file stream` · `404 Not Found`

---

### GET `/files/{file_id}`  🔒 Admin
Get one file record's full metadata.

**Responses:** `200 FileResponse` · `404 Not Found`

---

### GET `/files/{file_id}/status`  🔒 Admin
Lightweight poll for an in-flight upload.

**Response 200**
```json
{
  "id": 7,
  "processing_status": "pending",
  "processing_error": null,
  "vector_store_file_id": null
}
```

`processing_status` values: `pending` · `processing` · `completed` · `failed`

---

### GET `/files/{file_id}/download`  🔒 Admin
Download a file's original content by its ID.

**Responses:** `200 — file stream` · `404 Not Found`

---

### GET `/files/{file_id}/versions`  🔒 Admin
All versions of the document this file belongs to, newest first.  
Includes version number, label, effective date, `is_latest`, and processing status.

---

### PATCH `/files/{file_id}/status`  🔒 Admin
Enable or disable a file in the knowledge base.

| Param | Type | Required | Description |
|-------|------|----------|-------------|
| `status_state` | string | ✅ | `ACTIVE`, `DISABLE`, or `INACTIVE` |

`DISABLE`/`INACTIVE` moves vector chunks out of search. `ACTIVE` restores them.

---

### POST `/files/{file_id}/reindex`  🔒 Admin
Drop and re-parse a file's vector chunks.  
Returns immediately (`202`). Poll `GET /files/{file_id}/status` to track progress.

**Responses:** `202 Accepted` · `409 Conflict` (file is deleted)

---

### DELETE `/files/{file_id}`  🔒 Admin
Delete a file record and its KB document.  
`file_id` accepts an integer (SQL file record) or a UUID (KB vector-store document).

**Responses:** `200 OK` · `422 Unprocessable`

---

## DMS (Document Management)

> Read-only. Writes go through the DMS service directly.

---

### GET `/dms/documents`
Paginated list of DMS documents.

| Param | Type | Default | Description |
|-------|------|---------|-------------|
| `dms_category_id` | int | — | Filter by category |
| `name` | string | — | Partial name match |
| `is_archived` | bool | `false` | Include archived documents |
| `page_number` | int | 1 | |
| `page_size` | int | 20 | Max 200 |

---

### GET `/dms/documents/{document_id}/versions`
All versions of one DMS document.

| Param | Type | Default | Description |
|-------|------|---------|-------------|
| `is_archived` | bool | `false` | Include archived versions |

---

### GET `/dms/versions/{version_id}`
Get a single DMS document version. Includes `pvc_path` and markdown content.

**Responses:** `200 OK` · `404 Not Found`

---

### GET `/dms/versions/{version_id}/download`
Stream the file for a DMS version off the store.

**Responses:** `200 — file stream` · `404 Not Found` (no `pvc_path`) · `410 Gone` (file removed from store)

---

## Prompt Management

### GET `/prompts`
List all pipeline prompt steps with their current template.

**Response 200**
```json
{
  "total_records": 3,
  "prompts": [{
    "key": "answer",
    "name": "...",
    "description": "...",
    "template": "...",
    "version": 1,
    "has_override": false
  }]
}
```

---

### GET `/prompts/{prompt_key}`
Get one prompt step's current template.

**Responses:** `200 OK` · `404 Not Found`

---

### PUT `/prompts/{prompt_key}`  🔒 Admin
Override a prompt step's system instruction.

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `template` | string | ✅ | Custom instruction, min 5 chars. Prepended to the built-in prompt on every chat turn; takes effect immediately — no restart needed. |

**Responses:** `200 OK` · `404 Not Found`

---

### POST `/prompts/{prompt_key}/reset`  🔒 Admin
Remove a custom override — reverts to the built-in template.

**Responses:** `200 OK` · `404 Not Found`

---

## Admin

> All `/admin` routes require **admin** role.

---

### GET `/admin/audit`
Paginated audit trail — uploads, deletes, refused duplicates.

| Param | Type | Default | Description |
|-------|------|---------|-------------|
| `action` | string | — | e.g. `file_upload`, `file_delete`, `file_duplicate`, `audit_purge` |
| `actor` | string | — | Filter by email |
| `page_number` | int | 1 | |
| `page_size` | int | 50 | Max 500 |

---

### GET `/admin/metrics`
Chat quality & health metrics.

| Param | Type | Default | Description |
|-------|------|---------|-------------|
| `days` | int (1–365) | 7 | Window for turns, errors, latency, grounding verdicts, thumbs up/down |

---

### GET `/admin/chat-trace/{history_id}`
Full debug trace of one chat turn: question, answer, queries searched, passages retrieved/reranked, outcome, model version, and feedback.

**Responses:** `200 OK` · `404 Not Found`

---

### POST `/admin/purge`
Apply retention settings now — deletes audit-log and chat-trace rows older than `AUDIT_RETENTION_DAYS` / `TRACE_RETENTION_DAYS`. Also runs automatically at startup.

---

### POST `/admin/versions/sync`
Backfills document keys and version numbers for files uploaded before versioning, then re-elects the latest version of every document. Safe to run any time; run once after upgrading.

---

### POST `/admin/files/backfill-hashes`
Reads each un-hashed file from storage and records its SHA-256. Safe to re-run. Unreadable files are listed and retried on the next run.

---

## Error Codes

| Code | Meaning | Common Cause |
|------|---------|--------------|
| `400` | Bad Request | Field validation failed |
| `401` | Unauthorized | Missing or expired Bearer token |
| `403` | Forbidden | Non-admin calling an admin-only endpoint |
| `404` | Not Found | Unknown ID, or resource owned by another user |
| `409` | Conflict | Duplicate file upload (same bytes already in KB) |
| `413` | Payload Too Large | File exceeds `MAX_UPLOAD_SIZE_MB` |
| `415` | Unsupported Media Type | Extension not in `ALLOWED_UPLOAD_EXTENSIONS` |
| `422` | Unprocessable | Body parsed but failed semantic validation |
| `429` | Rate Limited | Too many chat requests — back off and retry |
| `500` | Server Error | Unexpected backend failure |
| `503` | Unavailable | Feature disabled (e.g. chat profile with `DISABLE_AUTH`) |
