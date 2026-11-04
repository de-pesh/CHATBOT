# API Integration Reference — Chatbot (Front-End)

**Document type:** API integration reference  
**Audience:** Front-end team  
**Service:** main-service-chatbot

---

## Contents

- [Introduction](#introduction)
- [Integration Conventions](#integration-conventions)
- [Authentication](#authentication)
- [Chat](#chat)
- [Sessions](#sessions)
- [History](#history)
- [Feedback](#feedback)
- [Files](#files)
- [DMS Documents](#dms-documents)

---

## Introduction

This document covers the endpoints the front-end application needs to interact with the chatbot service. Admin-only operations (file upload, audit logs, prompt overrides) are excluded.

---

## Integration Conventions

### Base URL

```
http://chatbot-api.apps.dda-desc-az1-01.desccii.local
```

All routes below are appended directly to this base URL (no sub-path prefix).

### Authentication

Endpoints marked **Required** expect a bearer token in every request header:

```
Authorization: Bearer <access_token>
```

Obtain the token from `POST /authenticate`. Endpoints marked **Not required** can be called without a token.

### Error Handling

| HTTP | Meaning |
|------|---------|
| 400 | Bad request — check the request body. |
| 401 | Missing or expired token — re-authenticate. |
| 404 | Resource not found, or not owned by the caller. |
| 422 | Validation failure — check `detail[].loc` and `detail[].msg` in the response. |
| 429 | Rate limit reached on chat — back off and retry. |
| 500 | Server error. |

### Notation

- **Location** — `Path`, `Query`, or `Body`.
- **Required** — `Yes`, `No`, or `Conditional`.

---

## Authentication

### POST /authenticate

| | |
|--|--|
| **Purpose** | Log in with email + password and receive a bearer token. |
| **Method** | POST |
| **Route** | `/auth/authenticate` |
| **Authentication** | Not required |
| **Content type** | `application/x-www-form-urlencoded` |
| **Processing** | Synchronous |

#### Request Parameters

| Parameter | Location | Type | Required | Description |
|-----------|----------|------|----------|-------------|
| `username` | Form data | string (email) | Yes | The user's email address (sent as `username` per OAuth2 convention). |
| `password` | Form data | string | Yes | The user's password. |

#### Example Request

```
POST http://chatbot-api.apps.dda-desc-az1-01.desccii.local/auth/authenticate
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
| `access_token` | string | Pass as `Authorization: Bearer <token>` on every subsequent request. |
| `token_type` | string | Always `"bearer"`. |
| `role` | string | `"admin"` or `"user"`. |

#### Error Responses

| Error | HTTP | Meaning |
|-------|------|---------|
| Unauthorized | 401 | Wrong email or password. |

---

### POST /re-authenticate

| | |
|--|--|
| **Purpose** | Refresh the session token without re-entering the password. |
| **Method** | POST |
| **Route** | `/auth/re-authenticate` |
| **Authentication** | Required |
| **Processing** | Synchronous |

Returns the same response shape as `POST /authenticate`. Call this before the token expires to keep the user's session alive without forcing a re-login.

---

### POST /change-password

| | |
|--|--|
| **Purpose** | Change the authenticated user's own password. |
| **Method** | POST |
| **Route** | `/auth/change-password` |
| **Authentication** | Required |
| **Content type** | `application/json` |
| **Processing** | Synchronous |

#### Request Parameters

| Parameter | Location | Type | Required | Description |
|-----------|----------|------|----------|-------------|
| `old_password` | Body | string | Yes | Current password. |
| `new_password` | Body | string (≥ 8 chars) | Yes | New password. |
| `confirm_new_password` | Body | string | Yes | Must match `new_password`. |

#### Example Request

```json
{
  "old_password": "OldP@ss1",
  "new_password": "NewP@ss2",
  "confirm_new_password": "NewP@ss2"
}
```

#### Success Response — HTTP 200

```json
{ "message": "Password changed successfully." }
```

---

## Chat

### POST /chat

| | |
|--|--|
| **Purpose** | Send a message and receive the full answer in one response. Use this for non-streaming clients. |
| **Method** | POST |
| **Route** | `/chat` |
| **Authentication** | Required |
| **Content type** | `application/json` |
| **Processing** | Synchronous — blocks until the full answer is ready. |

#### Request Parameters

| Parameter | Location | Type | Required | Description |
|-----------|----------|------|----------|-------------|
| `conversation_id` | Body | string \| integer \| null | No | Existing session ID. Omit or pass `null` to start a new session. Pass the value returned by the previous call to continue the conversation. |
| `chat` | Body | array of `{role, content}` | Yes | Message history. Each item has `role` (`"user"` or `"assistant"`) and `content` (string). The last item must be the current user turn. |
| `local_hour` | Body | integer (0–23) | No | The user's local hour, gives the assistant time-of-day context. |

#### Example Request

```json
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
  "response": "According to the policy [1], data must be retained for 7 years...",
  "conversation_id": "abc-123",
  "history_id": "def-456",
  "timings": {
    "rephrase_ms": 120,
    "retrieve_ms": 340,
    "rerank_ms": 80,
    "generate_ms": 1850,
    "pipeline_ms": 2400
  },
  "suggested_questions": [
    "How long can we keep employee records?",
    "What qualifies as personal data?"
  ]
}
```

#### Response Fields

| Field | Type | Description |
|-------|------|-------------|
| `response` | string | The assistant's answer. Inline `[n]` markers are replaced with citation links. |
| `conversation_id` | string | Session ID. Store and send back on the next turn to continue the thread. |
| `history_id` | string | ID of this answer. Use for feedback (`POST /chat/history/{history_id}/feedback`). |
| `timings` | object | Milliseconds per pipeline stage. Useful for performance display. |
| `suggested_questions` | string[] | Follow-up questions the assistant suggests. |

#### Error Responses

| Error | HTTP | Meaning |
|-------|------|---------|
| Not Found | 404 | `conversation_id` does not exist or is not owned by the caller. |
| Too Many Requests | 429 | Rate limit reached. Back off and retry. |

---

### POST /chat/stream

| | |
|--|--|
| **Purpose** | Same as `POST /chat` but the answer is streamed as Server-Sent Events (SSE) so text appears as it is generated. Preferred for interactive chat UIs. |
| **Method** | POST |
| **Route** | `/chat/stream` |
| **Authentication** | Required |
| **Content type** | `application/json` (request) / `text/event-stream` (response) |
| **Processing** | Streaming. HTTP status is always 200 once streaming starts; failures are reported as an `error` event. |

#### Request

Identical to `POST /chat`.

#### SSE Event Sequence

Events arrive in this order:

| Event | Payload fields | Purpose |
|-------|---------------|---------|
| `meta` | `conversation_id` | Sent first. Use this to track the new session before any text arrives. |
| `stage` | `stage` | Pipeline progress: `rephrasing`, `retrieving`, `reranking`, `generating`, `finalising`. Use for a progress indicator. |
| `delta` | `text` | Next chunk of the answer. Concatenate for a live preview. |
| `done` | `response`, `conversation_id`, `history_id`, `timings`, `suggested_questions` | Final event. `response` is authoritative — it is translated and has citation links resolved. Replace the concatenated deltas with this. |
| `error` | `detail`, `conversation_id` | The turn failed. May arrive after some `delta` events. Show an error to the user. |

#### Example SSE Output

```
event: meta
data: {"conversation_id": "abc-123"}

event: stage
data: {"stage": "generating"}

event: delta
data: {"text": "According to the policy"}

event: delta
data: {"text": ", data must be retained for 7 years..."}

event: done
data: {"response": "According to the policy [1], data must be retained for 7 years...", "conversation_id": "abc-123", "history_id": "def-456", "timings": {...}, "suggested_questions": [...]}
```

#### Client Implementation Notes

- Open the connection with `EventSource` or `fetch` with `ReadableStream`.
- On `meta`: store `conversation_id` immediately.
- On each `delta`: append `text` to the preview area.
- On `done`: replace the preview with `response` (authoritative, includes citation links).
- On `error`: display `detail` to the user and stop streaming.

---

### GET /chat/me

| | |
|--|--|
| **Purpose** | Get the caller's chat profile — their display name and preferences. |
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
    "role": "Compliance officer"
  }
}
```

---

### PATCH /chat/me

| | |
|--|--|
| **Purpose** | Update the caller's display name and/or assistant preferences. Fields omitted are left unchanged. |
| **Method** | PATCH |
| **Route** | `/chat/me` |
| **Authentication** | Required |
| **Content type** | `application/json` |
| **Processing** | Synchronous |

#### Request Parameters

| Parameter | Location | Type | Required | Description |
|-----------|----------|------|----------|-------------|
| `display_name` | Body | string \| null | Conditional | New display name. An empty string `""` clears it. |
| `preferences` | Body | object \| null | Conditional | Replaces all preferences. `{}` clears them. Keys ≤ 50 chars, values ≤ 200 chars, max 10 entries. |

At least one of `display_name` or `preferences` must be provided.

#### Example Request

```json
{
  "display_name": "Jane",
  "preferences": { "role": "Compliance officer" }
}
```

Returns the updated `ChatUserProfile` (same shape as `GET /chat/me`).

---

## Sessions

### GET /chat/session

| | |
|--|--|
| **Purpose** | List the caller's own chat sessions, paginated. |
| **Method** | GET |
| **Route** | `/chat/session` |
| **Authentication** | Required |
| **Processing** | Synchronous |

#### Query Parameters

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `id` | string \| integer | No | Filter by session ID. |
| `name` | string | No | Partial name match. |
| `is_active` | boolean | No | Default `true` (active sessions only). |
| `page_number` | integer ≥ 1 | No | Default 1. |
| `page_size` | integer 1–100 | No | Default 10. |

#### Success Response — HTTP 200

```json
{
  "total_records": 5,
  "page_number": 1,
  "page_size": 10,
  "data": [
    {
      "id": "abc-123",
      "name": "Data Retention Questions",
      "is_active": true,
      "created_on": "2026-11-03T09:00:00Z",
      "created_by": "jane.doe@example.com"
    }
  ]
}
```

---

### PATCH /chat/session/{session_id}

| | |
|--|--|
| **Purpose** | Rename one of the caller's own sessions. |
| **Method** | PATCH |
| **Route** | `/chat/session/{session_id}` |
| **Authentication** | Required |
| **Content type** | `application/json` |
| **Processing** | Synchronous |

#### Path Parameters

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `session_id` | string | Yes | Session to rename. Must be owned by the caller. |

#### Request Body

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `name` | string | Yes | New session name. |

#### Success Response — HTTP 200

```json
{ "message": "Session abc-123 updated successfully." }
```

#### Error Responses

| Error | HTTP | Meaning |
|-------|------|---------|
| Not Found | 404 | Session does not exist or is not owned by the caller. |

---

### DELETE /chat/session/{session_id}

| | |
|--|--|
| **Purpose** | Delete one of the caller's own sessions (and its history). |
| **Method** | DELETE |
| **Route** | `/chat/session/{session_id}` |
| **Authentication** | Required |
| **Processing** | Synchronous |

Returns **HTTP 204 No Content** on success.

#### Error Responses

| Error | HTTP | Meaning |
|-------|------|---------|
| Not Found | 404 | Session does not exist or is not owned by the caller. |

---

## History

### GET /chat/history

| | |
|--|--|
| **Purpose** | Fetch the caller's paginated chat history with optional filters. |
| **Method** | GET |
| **Route** | `/chat/history` |
| **Authentication** | Required |
| **Processing** | Synchronous |

#### Query Parameters

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `conversation_id` | string \| integer | No | Filter to one session's history. |
| `id` | string \| integer | No | Filter by a specific history row ID. |
| `prompt` | string | No | Partial match on the question text. |
| `response` | string | No | Partial match on the answer text. |
| `is_active` | boolean | No | Filter by active status. |
| `page_number` | integer ≥ 1 | No | Default 1. |
| `page_size` | integer 1–100 | No | Default 10. |

#### Success Response — HTTP 200

```json
{
  "total_records": 20,
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
| `id` | string | History row ID. Use in the feedback endpoint. |
| `prompt` | string | The user's question. |
| `response` | string \| null | The assistant's answer, or `null` if an error occurred. |
| `error` | string \| null | Error message if the turn failed. |
| `grounding_verdict` | string \| null | `"grounded"`, `"partial"`, or `"unsupported"` — whether citations support the answer. |

---

## Feedback

### POST /chat/history/{history_id}/feedback

| | |
|--|--|
| **Purpose** | Rate an answer thumbs up or down with an optional comment. Re-submitting for the same turn replaces the previous rating. |
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

| Parameter | Location | Type | Required | Description |
|-----------|----------|------|----------|-------------|
| `rating` | Body | string | Yes | `"positive"` (thumbs up) or `"negative"` (thumbs down). |
| `comment` | Body | string \| null | No | Optional detail. Recommended alongside a `"negative"` rating. Max 1 000 chars. |

#### Example Request

```json
{
  "rating": "negative",
  "comment": "The cited document does not mention a 7-year period."
}
```

#### Success Response — HTTP 200

```json
{ "message": "Feedback recorded." }
```

#### Error Responses

| Error | HTTP | Meaning |
|-------|------|---------|
| Not Found | 404 | `history_id` does not exist or is not owned by the caller. |

---

## Files

### GET /files/download-by-name

| | |
|--|--|
| **Purpose** | Download a knowledge-base file by its original name. Used to serve the files linked in chat citation markers. |
| **Method** | GET |
| **Route** | `/files/download-by-name` |
| **Base URL** | `http://chatbot-api.apps.dda-desc-az1-01.desccii.local` |
| **Authentication** | Required (any role) |
| **Processing** | Synchronous — streams the file bytes. |

#### Query Parameters

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `name` | string | Yes | Original file name (e.g. `data_retention_policy_v3.2.pdf`). |
| `path` | string | No | Stored blob path, if already known (comes from the citation link). Skips the database lookup. |

The `name` and `path` values for citation links are embedded in the `response` text returned by the chat endpoints. Parse them from the `href` attributes of the anchor tags in the response HTML.

#### Success Response

Binary file stream with:
```
Content-Disposition: attachment; filename="data_retention_policy_v3.2.pdf"
```

#### Error Responses

| Error | HTTP | Meaning |
|-------|------|---------|
| Not Found | 404 | File name not found in the database or blob storage. |

---

## DMS Documents

Read-only access to Document Management System (DMS) content. The chatbot surfaces DMS documents; these endpoints let the front end browse and download them directly.

### GET /dms/documents

| | |
|--|--|
| **Purpose** | Paginated list of DMS documents. |
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
    {
      "id": 1,
      "name": "Data Retention Policy",
      "dms_category_id": 3,
      "is_archived": false
    }
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

Returns a list of version objects with `{ id, version_number, version_label, effective_date, is_archived }`.

---

### GET /dms/versions/{version_id}

| | |
|--|--|
| **Purpose** | Full detail for one DMS document version. |
| **Method** | GET |
| **Route** | `/dms/versions/{version_id}` |
| **Authentication** | Required |
| **Processing** | Synchronous |

Returns 404 if the version does not exist.

---

### GET /dms/versions/{version_id}/download

| | |
|--|--|
| **Purpose** | Stream the file for a DMS document version. |
| **Method** | GET |
| **Route** | `/dms/versions/{version_id}/download` |
| **Authentication** | Required |
| **Processing** | Synchronous — streams file bytes. |

#### Error Responses

| Error | HTTP | Meaning |
|-------|------|---------|
| Not Found | 404 | Version not found, or no file path set on the version. |
| Gone | 410 | File was registered but is no longer present on the store. |
