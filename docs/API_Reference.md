# API Integration Reference — Chatbot Service

**Document type:** API integration reference  
**Service:** main-service-chatbot  

---

## Contents

- [Introduction](#introduction)
- [Integration Conventions](#integration-conventions)
- [Endpoint Specifications](#endpoint-specifications)
  - [Authentication](#authentication-endpoints)
  - [Chat](#chat-endpoints)
  - [Files (Knowledge Base)](#files-knowledge-base-endpoints)
  - [Prompts](#prompts-endpoints)
  - [DMS Documents](#dms-document-endpoints)
  - [Admin](#admin-endpoints)
  - [Internal](#internal-endpoints)

---

## Introduction

### Purpose

This document specifies every HTTP endpoint exposed by the chatbot's main service. It covers authentication, the chat pipeline (blocking and streaming), knowledge-base file management, system-prompt overrides, DMS document browsing, and admin/observability operations.

### Scope

All endpoints listed here are implemented and available. Payload schemas, field names, and data types are derived directly from the running code and are authoritative. Example values are illustrative.

---

## Integration Conventions

### Base URL

| Service | Base URL |
|---------|----------|
| main-service | `http://<host>/main-service` |

All routes below are appended to the base URL.

### Authentication

- Endpoints marked **Required** expect a bearer token in the `Authorization` header:  
  `Authorization: Bearer <access_token>`
- Obtain the token from `POST /authenticate`.
- Endpoints marked **Not required** can be called without a token.
- Endpoints marked **Admin only** require the authenticated user to have `role = "admin"`.

### Roles

| Role | What it can do |
|------|---------------|
| `user` | Chat, view own sessions/history, download cited files |
| `admin` | Everything above, plus upload/delete files, manage prompts, view audit and metrics |

### Processing Modes

- **Synchronous** — the result is returned in the HTTP response body.
- **Asynchronous** — the request is accepted and queued; the caller polls a status endpoint until work completes. Asynchronous operations: file upload (parse + index) and file reindex.
- **Streaming (SSE)** — `POST /chat/stream` returns `text/event-stream`. The answer appears chunk by chunk as the LLM generates it.

### Error Handling

Standard HTTP status codes are used. Validation errors return `422` with a `detail[]` array of `{loc, msg, type}` objects. Business-logic errors return `400`, `404`, `409`, or `415` with a `detail` string.

### Notation

- **Location** — where the parameter is supplied: `Path`, `Query`, `Body`, or `Form data`.
- **Size** — maximum length in characters (chars), kilobytes (KB), megabytes (MB), or bytes. A dash (—) means no size limit applies.
- **Required** — `Yes`, `No`, or `Conditional` where the rule is explained in the description.

---

## Endpoint Specifications

---

## Authentication Endpoints

### POST /authenticate

| | |
|--|--|
| **Purpose** | Authenticate with email + password; receive a bearer token. |
| **Method** | POST |
| **Route** | `/authenticate` |
| **Authentication** | Not required |
| **Content type** | `application/x-www-form-urlencoded` (OAuth2 password form) |
| **Processing** | Synchronous |

#### Request Parameters

| Parameter | Location | Type | Size | Required | Description |
|-----------|----------|------|------|----------|-------------|
| `username` | Form data | string (email) | 1–254 chars | Yes | The user's email address (sent as `username` per OAuth2 convention). |
| `password` | Form data | string | 1–255 chars | Yes | The user's password. |

#### Example Request

```
POST /authenticate
Content-Type: application/x-www-form-urlencoded

username=jane.doe@example.com&password=s3cr3t!
```

#### Success Response — HTTP 200

```json
{
  "access_token": "<jwt>",
  "token_type": "bearer",
  "role": "user"
}
```

#### Response Fields

| Field | Type | Description |
|-------|------|-------------|
| `access_token` | string | Bearer token. Pass in `Authorization: Bearer <token>` on every subsequent call. |
| `token_type` | string | Always `"bearer"`. |
| `role` | string | `"admin"` or `"user"`. Determines which endpoints the caller may reach. |

#### Error Responses

| Error | HTTP | Meaning / Handling |
|-------|------|-------------------|
| Unauthorized | 401 | Wrong email or password. |

---

### POST /re-authenticate

| | |
|--|--|
| **Purpose** | Extend the caller's session by issuing a fresh token without re-entering the password. |
| **Method** | POST |
| **Route** | `/re-authenticate` |
| **Authentication** | Required |
| **Processing** | Synchronous |

Returns the same `Token` shape as `/authenticate`. Call this before the current token expires to avoid interrupting a session.

---

### POST /change-password

| | |
|--|--|
| **Purpose** | Change the authenticated user's own password. |
| **Method** | POST |
| **Route** | `/change-password` |
| **Authentication** | Required |
| **Content type** | `application/json` |
| **Processing** | Synchronous |

#### Request Parameters

| Parameter | Location | Type | Size | Required | Description |
|-----------|----------|------|------|----------|-------------|
| `old_password` | Body | string | — | Yes | Current password. |
| `new_password` | Body | string | ≥ 8 chars | Yes | Replacement password. |
| `confirm_new_password` | Body | string | — | Yes | Must match `new_password`. |

#### Success Response — HTTP 200

```json
{ "message": "Password changed successfully." }
```

---

### POST /users

| | |
|--|--|
| **Purpose** | Admin-only. Create a new user account. |
| **Method** | POST |
| **Route** | `/users` |
| **Authentication** | Admin only |
| **Content type** | `application/json` |
| **Processing** | Synchronous |

#### Request Parameters

| Parameter | Location | Type | Size | Required | Description |
|-----------|----------|------|------|----------|-------------|
| `first_name` | Body | string | 1–255 chars | Yes | User's first name. |
| `last_name` | Body | string | 1–255 chars | Yes | User's last name. |
| `email` | Body | string (email) | 1–254 chars | Yes | Must be unique. |
| `password` | Body | string | ≥ 8 chars | Yes | Initial password. |
| `role` | Body | string | — | No | `"admin"` or `"user"` (default `"user"`). |
| `is_active` | Body | boolean | — | No | `true` by default. |

#### Example Request

```
POST /users
Authorization: Bearer <access_token>
Content-Type: application/json

{
  "first_name": "Jane",
  "last_name": "Doe",
  "email": "jane.doe@example.com",
  "password": "SecureP@ss1",
  "role": "user"
}
```

#### Success Response — HTTP 201

```json
{
  "id": "33333333-3333-3333-3333-333333333333",
  "first_name": "Jane",
  "last_name": "Doe",
  "email": "jane.doe@example.com",
  "role": "user",
  "is_active": true,
  "created_on": "2026-11-03T09:00:00Z"
}
```

#### Error Responses

| Error | HTTP | Meaning / Handling |
|-------|------|-------------------|
| Unauthorized | 401 | Missing or invalid token. |
| Forbidden | 403 | Caller is not an admin. |
| Unprocessable Entity | 422 | Validation failure. Check `detail[].loc` and `detail[].msg`. |

---

## Chat Endpoints

### POST /chat

| | |
|--|--|
| **Purpose** | Send a chat message and receive the complete answer in one response. |
| **Method** | POST |
| **Route** | `/chat` |
| **Authentication** | Required |
| **Content type** | `application/json` |
| **Processing** | Synchronous — blocks until the full answer is ready. Prefer `/chat/stream` for interactive UIs. |

#### Request Parameters

| Parameter | Location | Type | Size | Required | Description |
|-----------|----------|------|------|----------|-------------|
| `conversation_id` | Body | string \| integer \| null | — | No | Existing session ID. Omit (or pass `null`) to start a new session. Pass the `conversation_id` returned by a previous call to continue a thread. |
| `chat` | Body | array | — | Yes | Message array. Each element has `role` (`"user"` or `"assistant"`) and `content` (string). The last element must be the current user turn. |
| `local_hour` | Body | integer | 0–23 | No | The user's local hour, used to give the assistant time-of-day context (e.g. "good morning"). |

#### Example Request

```
POST /chat
Authorization: Bearer <access_token>
Content-Type: application/json

{
  "conversation_id": null,
  "chat": [
    { "role": "user", "content": "What is the data retention policy?" }
  ],
  "local_hour": 9
}
```

#### Success Response — HTTP 200

```json
{
  "response": "According to the policy [1], personal data must be retained for no more than 7 years...",
  "conversation_id": "abc-123",
  "history_id": "def-456",
  "timings": {
    "rephrase_ms": 120,
    "retrieve_ms": 340,
    "rerank_ms": 80,
    "first_token_ms": 510,
    "generate_ms": 1850,
    "pipeline_ms": 2400
  },
  "suggested_questions": [
    "How long can we keep employee records?",
    "What qualifies as personal data under the policy?"
  ]
}
```

#### Response Fields

| Field | Type | Description |
|-------|------|-------------|
| `response` | string | The assistant's answer, with inline citation links replacing `[n]` markers. |
| `conversation_id` | string | Session ID. Store and send this back on the next turn to continue the conversation. |
| `history_id` | string | Unique ID of this answer row. Use it for feedback (`POST /chat/history/{history_id}/feedback`). |
| `timings` | object | Milliseconds per pipeline stage. Useful for diagnosing slow turns. |
| `suggested_questions` | string[] | Follow-up questions the assistant suggests the user might ask next. |

#### Error Responses

| Error | HTTP | Meaning / Handling |
|-------|------|-------------------|
| Unauthorized | 401 | Missing or invalid token. |
| Not Found | 404 | `conversation_id` does not exist or is not owned by the caller. |
| Too Many Requests | 429 | Rate limit reached. Back off and retry. |

---

### POST /chat/stream

| | |
|--|--|
| **Purpose** | Same pipeline as `POST /chat`, but the answer is delivered as Server-Sent Events so it appears as it is generated. |
| **Method** | POST |
| **Route** | `/chat/stream` |
| **Authentication** | Required |
| **Content type** | `application/json` (request) / `text/event-stream` (response) |
| **Processing** | Streaming. HTTP status is always 200 once streaming starts; failures appear as an `error` event. |

#### Request

Identical to `POST /chat`.

#### SSE Event Sequence

| Event | Payload | When |
|-------|---------|------|
| `meta` | `{ "conversation_id": "abc-123" }` | First event, before any text. Lets the client track the session immediately. |
| `stage` | `{ "stage": "rephrasing" \| "retrieving" \| "reranking" \| "generating" \| "finalising" }` | Progress updates for each pipeline stage. Use for a progress indicator. |
| `delta` | `{ "text": "According to..." }` | Each chunk of the answer as it arrives. Concatenate for a live preview. |
| `done` | `{ "response", "conversation_id", "history_id", "collections", "timings", "suggested_questions" }` | Final event. `response` is authoritative (translated + citation links resolved) — replace the concatenated deltas with it. |
| `error` | `{ "detail": "...", "conversation_id": "..." }` | The turn failed. May arrive after some `delta` events. |

#### Example SSE Frame

```
event: delta
data: {"text": "According to the data policy"}

event: done
data: {"response": "According to the data policy [1]...", "conversation_id": "abc-123", "history_id": "def-456", "timings": {...}, "suggested_questions": [...]}
```

---

### GET /chat/me

| | |
|--|--|
| **Purpose** | Return the caller's chat profile — their display name and assistant preferences. |
| **Method** | GET |
| **Route** | `/chat/me` |
| **Authentication** | Required |
| **Processing** | Synchronous |

#### Success Response — HTTP 200

```json
{
  "id": "33333333-3333-3333-3333-333333333333",
  "email": "jane.doe@example.com",
  "display_name": "Jane",
  "preferences": {
    "role": "Compliance officer",
    "language": "French"
  }
}
```

---

### PATCH /chat/me

| | |
|--|--|
| **Purpose** | Update the caller's display name and/or assistant preferences. Fields omitted are unchanged. |
| **Method** | PATCH |
| **Route** | `/chat/me` |
| **Authentication** | Required |
| **Content type** | `application/json` |
| **Processing** | Synchronous |

#### Request Parameters

| Parameter | Location | Type | Size | Required | Description |
|-----------|----------|------|------|----------|-------------|
| `display_name` | Body | string \| null | ≤ 100 chars | Conditional | New display name. An empty string clears it. |
| `preferences` | Body | object \| null | ≤ 10 entries; keys ≤ 50 chars, values ≤ 200 chars | Conditional | Replaces all preferences. `{}` clears them. At least one of `display_name` or `preferences` must be supplied. |

---

### GET /chat/history

| | |
|--|--|
| **Purpose** | Fetch the caller's paginated chat history, with optional filters. |
| **Method** | GET |
| **Route** | `/chat/history` |
| **Authentication** | Required |
| **Processing** | Synchronous |

#### Query Parameters

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `id` | integer \| string | No | Filter by a specific history row ID. |
| `conversation_id` | integer \| string | No | Filter by session. |
| `prompt` | string | No | Partial match on the question text. |
| `response` | string | No | Partial match on the answer text. |
| `is_active` | boolean | No | Filter by active status. |
| `page_number` | integer ≥ 1 | No | Page to retrieve (default 1). |
| `page_size` | integer 1–100 | No | Records per page (default 10). |

#### Success Response — HTTP 200

```json
{
  "total_records": 42,
  "page_number": 1,
  "page_size": 10,
  "data": [
    {
      "id": "def-456",
      "conversation_id": "abc-123",
      "prompt": "What is the data retention policy?",
      "response": "According to the policy [1]...",
      "error": null,
      "is_active": true,
      "created_on": "2026-11-03T09:01:00Z",
      "created_by": "jane.doe@example.com",
      "rag_collections": "main",
      "grounding_verdict": "grounded",
      "grounding_detail": null
    }
  ]
}
```

#### Response Fields

| Field | Type | Description |
|-------|------|-------------|
| `rag_collections` | string \| null | Comma-separated vector collections searched to produce this answer. |
| `grounding_verdict` | string \| null | `"grounded"`, `"partial"`, or `"unsupported"` — whether the cited passages actually support the answer. |
| `grounding_detail` | string \| null | Short sentence describing what was unsupported (for `"partial"` or `"unsupported"` verdicts). |

---

### GET /chat/session

| | |
|--|--|
| **Purpose** | List the caller's chat sessions, paginated. |
| **Method** | GET |
| **Route** | `/chat/session` |
| **Authentication** | Required |
| **Processing** | Synchronous |

#### Query Parameters

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `id` | string \| integer | No | Filter by session ID. |
| `name` | string | No | Partial name match. |
| `is_active` | boolean | No | Defaults to `true` (active sessions only). |
| `page_number` | integer ≥ 1 | No | Default 1. |
| `page_size` | integer 1–100 | No | Default 10. |

---

### PATCH /chat/session/{session_id}

| | |
|--|--|
| **Purpose** | Rename one of the caller's own chat sessions. |
| **Method** | PATCH |
| **Route** | `/chat/session/{session_id}` |
| **Authentication** | Required |
| **Content type** | `application/json` |
| **Processing** | Synchronous |

#### Path Parameters

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `session_id` | string | Yes | ID of the session to rename. Must be owned by the caller. |

#### Request Body

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `name` | string | Yes | New display name for the session. |

#### Success Response — HTTP 200

```json
{ "message": "Session abc-123 updated successfully." }
```

#### Error Responses

| Error | HTTP | Meaning / Handling |
|-------|------|-------------------|
| Not Found | 404 | Session does not exist or is not owned by the caller. |

---

### DELETE /chat/session/{session_id}

| | |
|--|--|
| **Purpose** | Soft-delete one of the caller's own sessions (and its history). |
| **Method** | DELETE |
| **Route** | `/chat/session/{session_id}` |
| **Authentication** | Required |
| **Processing** | Synchronous |

Returns **HTTP 204 No Content** on success.

---

### POST /chat/history/{history_id}/feedback

| | |
|--|--|
| **Purpose** | Submit a thumbs up or thumbs down (with an optional comment) on one of the caller's own answers. Re-submitting replaces the previous rating. |
| **Method** | POST |
| **Route** | `/chat/history/{history_id}/feedback` |
| **Authentication** | Required |
| **Content type** | `application/json` |
| **Processing** | Synchronous |

#### Path Parameters

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `history_id` | string | Yes | The `history_id` from the chat response. Must be owned by the caller. |

#### Request Parameters

| Parameter | Location | Type | Size | Required | Description |
|-----------|----------|------|------|----------|-------------|
| `rating` | Body | string | — | Yes | `"positive"` (thumbs up) or `"negative"` (thumbs down). |
| `comment` | Body | string \| null | ≤ 1 000 chars | No | Optional detail. Typical for a `"negative"` rating. |

#### Success Response — HTTP 200

```json
{ "message": "Feedback recorded." }
```

---

## Files (Knowledge Base) Endpoints

File operations (upload, delete, reindex, status management) are **admin only**. Downloading a file for citation use is available to all authenticated users.

### Upload Workflow

Uploads are asynchronous. After `POST /files/` returns the file record, `kb_worker` parses and indexes the content in the background. Poll `GET /files/{file_id}/status` until `processing_status` is `"completed"` or `"failed"`.

```
Step 1  POST /files/                  → file record (processing_status = "pending")
Step 2  GET  /files/{file_id}/status  → poll until "completed" or "failed"
```

---

### POST /files/

| | |
|--|--|
| **Purpose** | Upload one or more files to blob storage and create file records. Parsing and indexing happen off the request path. |
| **Method** | POST |
| **Route** | `/files/` |
| **Authentication** | Admin only |
| **Content type** | `multipart/form-data` |
| **Processing** | Synchronous upload, then async parse + index. Poll `GET /files/{file_id}/status`. |

#### Request Parameters

| Parameter | Location | Type | Size | Required | Description |
|-----------|----------|------|------|----------|-------------|
| `files` | Form data | file[] | ≤ configured MB per file | Yes | One or more files. Supported extensions set by `ALLOWED_UPLOAD_EXTENSIONS`. |
| `allow_duplicate` | Query | boolean | — | No | Default `false`. Set `true` to upload a file whose bytes are already in the knowledge base. Without it, a byte-identical file returns 409. |
| `version_label` | Query | string | ≤ 50 chars | No | Human-readable version name (e.g. `"3.2"`). Letters, digits, `.`, `_`, or `-`. Defaults to the tag in the file name (`Policy_v3.2.pdf`), else none. |
| `effective_date` | Query | string (date) | — | No | When this version takes effect (`YYYY-MM-DD`). |

#### Example Request

```
POST /files/?version_label=3.2&effective_date=2026-01-01
Authorization: Bearer <access_token>
Content-Type: multipart/form-data

files=@data_retention_policy_v3.2.pdf
```

#### Success Response — HTTP 201

```json
[
  {
    "id": 42,
    "name": "data_retention_policy_v3.2.pdf",
    "mimetype": "application/pdf",
    "file_path": "blob/storage/path/...",
    "extracted_text_path": null,
    "vector_store_file_id": null,
    "processing_status": "pending",
    "processing_error": null,
    "is_active": true,
    "document_key": "data_retention_policy",
    "version_number": 3,
    "is_latest": true,
    "version_label": "3.2",
    "effective_date": "2026-01-01",
    "created_on": "2026-11-03T09:05:00Z",
    "created_by": "admin@example.com"
  }
]
```

#### Response Fields

| Field | Type | Description |
|-------|------|-------------|
| `id` | integer | File record ID — use in all subsequent calls. |
| `processing_status` | string | `"pending"` on creation. Becomes `"processing"` → `"completed"` or `"failed"`. |
| `vector_store_file_id` | string \| null | Set once indexing completes. `null` until then. |
| `document_key` | string \| null | The document this file belongs to (name without extension or version tag). Files sharing the same `document_key` are versions of the same document. |
| `version_number` | integer | Sequential upload number: 1, 2, 3 … |
| `is_latest` | boolean | Whether this is the newest ingested version. Questions are answered from the latest by default. |

#### Error Responses

| Error | HTTP | Meaning / Handling |
|-------|------|-------------------|
| Unauthorized | 401 | Missing or invalid token. |
| Forbidden | 403 | Caller is not an admin. |
| Request Entity Too Large | 413 | File exceeds `MAX_UPLOAD_SIZE_MB`. |
| Unsupported Media Type | 415 | File extension is not in `ALLOWED_UPLOAD_EXTENSIONS`. |
| Conflict | 409 | Byte-identical file already exists. Delete the existing one or upload with `allow_duplicate=true`. |
| Unprocessable Entity | 422 | File failed content validation. |

---

### GET /files/

| | |
|--|--|
| **Purpose** | List all file records in the system. |
| **Method** | GET |
| **Route** | `/files/` |
| **Authentication** | Admin only |
| **Processing** | Synchronous |

Returns `list[FileResponse]` — the same shape as the `POST /files/` response body items.

---

### GET /files/{file_id}

| | |
|--|--|
| **Purpose** | Retrieve one file record's full metadata by its numeric ID. |
| **Method** | GET |
| **Route** | `/files/{file_id}` |
| **Authentication** | Admin only |
| **Processing** | Synchronous |

Returns `FileResponse`. Returns 404 if the file does not exist.

---

### GET /files/{file_id}/status

| | |
|--|--|
| **Purpose** | Lightweight poll target for an in-flight upload — just the processing status, not the full record. |
| **Method** | GET |
| **Route** | `/files/{file_id}/status` |
| **Authentication** | Admin only |
| **Processing** | Synchronous |

#### Success Response — HTTP 200

```json
{
  "id": 42,
  "processing_status": "completed",
  "processing_error": null,
  "vector_store_file_id": "vs_abc123"
}
```

#### Processing Status Lifecycle

| Status | Description |
|--------|-------------|
| `pending` | Uploaded; job enqueued but not yet picked up by `kb_worker`. |
| `processing` | `kb_worker` is actively parsing and indexing the file. |
| `completed` | Parsing done; `extracted_text_path` and `vector_store_file_id` are set. File is now searchable. |
| `failed` | Exhausted retries. See `processing_error` for the reason. |

---

### GET /files/{file_id}/versions

| | |
|--|--|
| **Purpose** | List every upload of the document this file belongs to, newest first. Includes deleted versions. |
| **Method** | GET |
| **Route** | `/files/{file_id}/versions` |
| **Authentication** | Admin only |
| **Processing** | Synchronous |

Returns a list of version objects: `{ version_number, version_label, effective_date, is_latest, processing_status }`.

---

### GET /files/{file_id}/download

| | |
|--|--|
| **Purpose** | Download the original file content by its numeric ID. |
| **Method** | GET |
| **Route** | `/files/{file_id}/download` |
| **Authentication** | Admin only |
| **Processing** | Synchronous — streams file bytes. |

Returns the file as a binary stream with `Content-Disposition: attachment`.

---

### GET /files/download-by-name

| | |
|--|--|
| **Purpose** | Download a file by its original name. Available to all authenticated users — intended for citation links in chat answers. |
| **Method** | GET |
| **Route** | `/files/download-by-name` |
| **Authentication** | Required (any role) |
| **Processing** | Synchronous — streams file bytes. |

#### Query Parameters

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `name` | string | Yes | Original file name (e.g. `data_retention_policy_v3.2.pdf`). |
| `path` | string | No | Stored blob path, if already known. Skips the database lookup. |

---

### DELETE /files/{file_id}

| | |
|--|--|
| **Purpose** | Delete a file record (and its knowledge-base chunks). Accepts either a numeric SQL file ID or a knowledge-base UUID. |
| **Method** | DELETE |
| **Route** | `/files/{file_id}` |
| **Authentication** | Admin only |
| **Processing** | Synchronous |

#### Path Parameters

| Parameter | Type | Description |
|-----------|------|-------------|
| `file_id` | string | An integer (SQL file ID) or UUID (knowledge-base document ID). |

#### Success Response — HTTP 200

```json
{ "message": "File record deleted successfully" }
```

---

### POST /files/{file_id}/reindex

| | |
|--|--|
| **Purpose** | Drop the file's existing vector chunks and parsed text, then queue a fresh parse-and-index pass. Returns immediately. |
| **Method** | POST |
| **Route** | `/files/{file_id}/reindex` |
| **Authentication** | Admin only |
| **Processing** | Asynchronous — returns HTTP 202. Poll `GET /files/{file_id}/status`. |

#### Success Response — HTTP 202

```json
{ "message": "File 42 queued for reindexing.", "file_id": 42 }
```

#### Error Responses

| Error | HTTP | Meaning / Handling |
|-------|------|-------------------|
| Conflict | 409 | File is soft-deleted and cannot be reindexed. |

---

### PATCH /files/{file_id}/status

| | |
|--|--|
| **Purpose** | Change a file's lifecycle status. `DISABLE`/`INACTIVE` moves the file's vector chunks out of the active search collection; `ACTIVE` moves them back. |
| **Method** | PATCH |
| **Route** | `/files/{file_id}/status` |
| **Authentication** | Admin only |
| **Processing** | Synchronous |

#### Query Parameters

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `status_state` | string | Yes | `ACTIVE`, `DISABLE`, or `INACTIVE`. |

#### Success Response — HTTP 200

```json
{
  "file_id": 42,
  "status": "DISABLE",
  "is_active": false,
  "chunks_moved": 87,
  "updated_by": "admin@example.com"
}
```

---

## Prompts Endpoints

Prompt endpoints let admins override the system instructions injected at each pipeline stage without restarting the service. Changes take effect on the next chat turn.

### GET /prompts

| | |
|--|--|
| **Purpose** | List all pipeline prompt steps with their current template text. |
| **Method** | GET |
| **Route** | `/prompts` |
| **Authentication** | Not required |
| **Processing** | Synchronous |

#### Success Response — HTTP 200

```json
{
  "total_records": 4,
  "prompts": [
    {
      "key": "answer",
      "name": "Answer Generation",
      "description": "System instruction for the final answer step.",
      "template": "You are a helpful assistant...",
      "version": 1,
      "has_override": false
    }
  ]
}
```

---

### GET /prompts/{prompt_key}

| | |
|--|--|
| **Purpose** | Get the current template for one prompt step. |
| **Method** | GET |
| **Route** | `/prompts/{prompt_key}` |
| **Authentication** | Not required |
| **Processing** | Synchronous |

Returns `PromptItem`. Returns 404 if the key is unknown.

---

### PUT /prompts/{prompt_key}

| | |
|--|--|
| **Purpose** | Admin only. Set a custom system instruction override for a prompt step. Prepended to the built-in template on every subsequent chat turn — no restart needed. |
| **Method** | PUT |
| **Route** | `/prompts/{prompt_key}` |
| **Authentication** | Admin only |
| **Content type** | `application/json` |
| **Processing** | Synchronous |

#### Request Parameters

| Parameter | Location | Type | Size | Required | Description |
|-----------|----------|------|------|----------|-------------|
| `template` | Body | string | ≥ 5 chars | Yes | The override text. Prepended to the built-in prompt. |

---

### POST /prompts/{prompt_key}/reset

| | |
|--|--|
| **Purpose** | Admin only. Remove the override and revert to the built-in template. |
| **Method** | POST |
| **Route** | `/prompts/{prompt_key}/reset` |
| **Authentication** | Admin only |
| **Processing** | Synchronous |

Returns the `PromptItem` with `has_override: false`.

---

## DMS Document Endpoints

Read-only access to DMS (Document Management System) documents and their versions. The chatbot consumes DMS content; all writes go through the DMS service.

### GET /dms/documents

| | |
|--|--|
| **Purpose** | Paginated list of DMS documents with optional filters. |
| **Method** | GET |
| **Route** | `/dms/documents` |
| **Authentication** | Required |
| **Processing** | Synchronous |

#### Query Parameters

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `dms_category_id` | integer | No | Filter by category. |
| `name` | string | No | Partial name match. |
| `is_archived` | boolean | No | Include archived documents (default `false`). |
| `page_number` | integer ≥ 1 | No | Default 1. |
| `page_size` | integer 1–200 | No | Default 20. |

#### Success Response — HTTP 200

```json
{
  "total_records": 15,
  "page_number": 1,
  "page_size": 20,
  "data": [
    { "id": 1, "name": "Data Retention Policy", "dms_category_id": 3, "is_archived": false }
  ]
}
```

---

### GET /dms/documents/{document_id}/versions

| | |
|--|--|
| **Purpose** | List all versions of a single DMS document. |
| **Method** | GET |
| **Route** | `/dms/documents/{document_id}/versions` |
| **Authentication** | Required |
| **Processing** | Synchronous |

#### Query Parameters

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `is_archived` | boolean | No | Include archived versions (default `false`). |

---

### GET /dms/versions/{version_id}

| | |
|--|--|
| **Purpose** | Get full detail for a single DMS document version, including `pvc_path` and any extracted markdown. |
| **Method** | GET |
| **Route** | `/dms/versions/{version_id}` |
| **Authentication** | Required |
| **Processing** | Synchronous |

Returns 404 if the version does not exist.

---

### GET /dms/versions/{version_id}/download

| | |
|--|--|
| **Purpose** | Stream the file stored for this DMS version from the PVC (or object store). |
| **Method** | GET |
| **Route** | `/dms/versions/{version_id}/download` |
| **Authentication** | Required |
| **Processing** | Synchronous — streams file bytes. |

#### Error Responses

| Error | HTTP | Meaning / Handling |
|-------|------|-------------------|
| Not Found | 404 | Version does not exist, or `pvc_path` is not set on it. |
| Gone | 410 | `pvc_path` is set but the file is no longer present on the store. |

---

## Admin Endpoints

All admin endpoints require `role = "admin"` and real user accounts (`DISABLE_AUTH = false`).

### GET /admin/audit

| | |
|--|--|
| **Purpose** | Admin audit trail — who uploaded, deleted, or was refused what, newest first. |
| **Method** | GET |
| **Route** | `/admin/audit` |
| **Authentication** | Admin only |
| **Processing** | Synchronous |

#### Query Parameters

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `action` | string | No | Filter by action (e.g. `file_upload`, `file_delete`, `file_duplicate`, `audit_purge`). Max 50 chars. |
| `actor` | string | No | Filter by email. Max 255 chars. |
| `page_number` | integer ≥ 1 | No | Default 1. |
| `page_size` | integer 1–500 | No | Default 50. |

#### Success Response — HTTP 200

```json
{
  "total_records": 120,
  "page_number": 1,
  "page_size": 50,
  "data": [
    {
      "action": "file_upload",
      "actor": "admin@example.com",
      "target": "42:data_retention_policy_v3.2.pdf",
      "detail": { "bytes": 204800, "sha256": "abc...", "version_no": 3 },
      "created_on": "2026-11-03T09:05:00Z"
    }
  ]
}
```

---

### GET /admin/metrics

| | |
|--|--|
| **Purpose** | Chat quality and health metrics over a rolling window. |
| **Method** | GET |
| **Route** | `/admin/metrics` |
| **Authentication** | Admin only |
| **Processing** | Synchronous |

#### Query Parameters

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `days` | integer 1–365 | No | Rolling window in days (default 7). |

#### Success Response — HTTP 200 (selected fields)

```json
{
  "days": 7,
  "total_turns": 342,
  "answered": 318,
  "errors": 5,
  "thumbs_up": 74,
  "thumbs_down": 12,
  "grounded": 290,
  "partial": 18,
  "unsupported": 10,
  "avg_latency_ms": 2140
}
```

---

### GET /admin/chat-trace/{history_id}

| | |
|--|--|
| **Purpose** | Full trace of one chat turn: the question, queries searched, passages retrieved and reranked, the answer, model version, and any feedback. |
| **Method** | GET |
| **Route** | `/admin/chat-trace/{history_id}` |
| **Authentication** | Admin only |
| **Processing** | Synchronous |

Returns 404 if the history row does not exist.

---

### POST /admin/purge

| | |
|--|--|
| **Purpose** | Apply retention settings immediately — delete audit-log and chat-trace rows older than `AUDIT_RETENTION_DAYS` / `TRACE_RETENTION_DAYS`. Also runs automatically at startup. |
| **Method** | POST |
| **Route** | `/admin/purge` |
| **Authentication** | Admin only |
| **Processing** | Synchronous |

#### Success Response — HTTP 200

```json
{ "audit_rows_removed": 42, "trace_rows_removed": 105 }
```

---

### POST /admin/versions/sync

| | |
|--|--|
| **Purpose** | Bring every document's version metadata up to date. Assigns document keys and version numbers to files uploaded before versioning was added, then re-elects the latest version of each document. Safe to run any time. |
| **Method** | POST |
| **Route** | `/admin/versions/sync` |
| **Authentication** | Admin only |
| **Processing** | Synchronous (may take time on large catalogs). |

---

### POST /admin/files/backfill-hashes

| | |
|--|--|
| **Purpose** | Compute and store content hashes for files uploaded before duplicate detection was introduced. Safe to run again; unreadable files are listed and retried next time. |
| **Method** | POST |
| **Route** | `/admin/files/backfill-hashes` |
| **Authentication** | Admin only |
| **Processing** | Synchronous. |

#### Success Response — HTTP 200

```json
{
  "processed": 120,
  "already_hashed": 80,
  "hashed_now": 38,
  "failed": 2,
  "unreadable": ["old_file.pdf"]
}
```

---

## Internal Endpoints

These endpoints are called by other services within the cluster, not by browsers or external clients. They are not authenticated and must not be exposed on a public route.

### POST /internal/catalog/refresh

| | |
|--|--|
| **Purpose** | Ask the main service to refresh its in-memory knowledge-base catalog descriptions. Called by `parsing-indexing-service` after each successful ingest. Fire-and-forget: returns immediately. |
| **Method** | POST |
| **Route** | `/internal/catalog/refresh` |
| **Authentication** | Not required (internal cluster only) |
| **Processing** | Asynchronous (refresh runs in background). |

#### Success Response — HTTP 200

```json
{ "ok": true }
```

| Field | Description |
|-------|-------------|
| `ok: true` | Refresh task queued. |
| `ok: false` | No catalog is running (catalog feature is disabled). |
