# Test Cases — Chatbot Platform

> Covers both services: **main-service-chatbot** (API / chat / files / admin) and
> **parsing-indexing-service** (document ingestion pipeline).
> Each case is written as: **Given / When / Then** where the scenario warrants it,
> or as a plain assertion where that is clearer.

---

## 1. Authentication & Authorization

### 1.1 JWT — login flow (DISABLE_AUTH=False)

| # | Scenario | Expected |
|---|----------|----------|
| 1.1.1 | POST `/auth/token` with valid email + password | 200, returns `access_token` and `token_type=bearer` |
| 1.1.2 | POST `/auth/token` with wrong password | 401 Unauthorized |
| 1.1.3 | POST `/auth/token` with unknown email | 401 Unauthorized |
| 1.1.4 | Any protected endpoint with valid Bearer token | Request proceeds normally |
| 1.1.5 | Any protected endpoint with expired token | 401 Unauthorized |
| 1.1.6 | Any protected endpoint with malformed / tampered token | 401 Unauthorized |
| 1.1.7 | Any protected endpoint with no Authorization header | 401 Unauthorized |
| 1.1.8 | Token issued with `ACCESS_TOKEN_EXPIRE_MINUTES` reaches its expiry | Subsequent request → 401 |

### 1.2 Disabled-auth mode (DISABLE_AUTH=True)

| # | Scenario | Expected |
|---|----------|----------|
| 1.2.1 | Any endpoint hit without a token | Treated as `dev@local.dev`, request proceeds |
| 1.2.2 | GET `/chat/me` with DISABLE_AUTH=True | 503 Service Unavailable |
| 1.2.3 | PATCH `/chat/me` with DISABLE_AUTH=True | 503 Service Unavailable |

### 1.3 Tenant context (TENANT_CONTEXT_ENABLED=True, DISABLE_AUTH=True)

| # | Scenario | Expected |
|---|----------|----------|
| 1.3.1 | Request includes `X-Entity-Id`, `X-Tenant-Id`, `X-User-Id`, `X-User-Type` headers + valid `X-Internal-Token` | Headers stored on the new chatbot_session row |
| 1.3.2 | Request missing `X-Internal-Token` (INTERNAL_API_SECRET set) | 401/403 rejected |
| 1.3.3 | TENANT_CONTEXT_ENABLED=False | Headers ignored, hardcoded demo GUIDs used |

### 1.4 Admin guard (`require_admin`)

| # | Scenario | Expected |
|---|----------|----------|
| 1.4.1 | Non-admin user calls POST `/files` | 403 Forbidden |
| 1.4.2 | Admin user calls POST `/files` | Proceeds to file validation |
| 1.4.3 | GET `/admin/audit` by non-admin | 403 Forbidden |

---

## 2. Chat — Core Pipeline (POST /chat and POST /chat/stream)

### 2.1 Session management

| # | Scenario | Expected |
|---|----------|----------|
| 2.1.1 | POST `/chat` with no `conversation_id` | New session created; `conversation_id` in response |
| 2.1.2 | POST `/chat` with existing `conversation_id` owned by caller | Turn appended to that session |
| 2.1.3 | POST `/chat` with `conversation_id` belonging to a different user | 404 Not Found |
| 2.1.4 | POST `/chat` with nonexistent `conversation_id` | 404 Not Found |
| 2.1.5 | Database failure during session creation | 500 with error detail |

### 2.2 Blocking endpoint (POST /chat)

| # | Scenario | Expected |
|---|----------|----------|
| 2.2.1 | Valid question in English | 200 with `response`, `conversation_id`, `history_id`, `timings`, `suggested_questions` |
| 2.2.2 | Question in non-English language | Response and `suggested_questions` translated back to query language |
| 2.2.3 | Question whose answer requires multiple citations | `[n]` markers in `response` converted to HTML links |
| 2.2.4 | LLM failure mid-turn | Error recorded on history row; HTTP 500 raised to caller |
| 2.2.5 | `local_hour` supplied (e.g. 8) | Greeting variant appropriate to time of day used |

### 2.3 Streaming endpoint (POST /chat/stream)

| # | Scenario | Expected |
|---|----------|----------|
| 2.3.1 | Valid question | SSE stream: `meta` → `stage:rephrasing` → `stage:retrieving` → `stage:reranking` → `stage:generating` → `delta`s → `done` |
| 2.3.2 | `done` event payload | Contains `response` (authoritative), `conversation_id`, `history_id`, `collections`, `timings`, `suggested_questions` |
| 2.3.3 | LLM error after streaming has started | `error` event emitted with `detail` and `conversation_id`; still HTTP 200 |
| 2.3.4 | Non-English question | `stage:finalising` emitted; translated answer in `done.response` |
| 2.3.5 | Simple greeting ("hi") with INTENT_ROUTING_ENABLED=True | Shortcut diversion, no search or rerank; answered immediately |
| 2.3.6 | Client disconnects mid-stream | Background tasks (grounding, trace, history write) still complete |

### 2.4 Intent routing

| # | Scenario | Expected |
|---|----------|----------|
| 2.4.1 | Short chitchat message (≤ INTENT_MAX_WORDS) | Intent classified as small talk; diverted without search |
| 2.4.2 | "What documents do you have?" with KB_OVERVIEW_ENABLED=True | Intent classified as `kb_overview`; catalog-listing reply |
| 2.4.3 | Same question with KB_OVERVIEW_ENABLED=False | Reply replaced with `NO_OVERVIEW_REPLY` |
| 2.4.4 | Substantive question (many words) | Bypasses intent check entirely; full pipeline runs |
| 2.4.5 | INTENT_ROUTING_ENABLED=False | All messages go through full pipeline regardless |

### 2.5 Evidence gate

| # | Scenario | Expected |
|---|----------|----------|
| 2.5.1 | Search returns no passages, NO_EVIDENCE_GATE_ENABLED=True | Turn short-circuited; `NO_EVIDENCE_REPLY` returned |
| 2.5.2 | Same, NO_EVIDENCE_GATE_ENABLED=False | Generation still attempted with empty context |
| 2.5.3 | All hits score below MIN_RELEVANCE_SCORE | Treated as no evidence |

### 2.6 Query rewrite & retrieval

| # | Scenario | Expected |
|---|----------|----------|
| 2.6.1 | Multi-part question | Rewrite produces multiple sub-queries (≤ REWRITE_MAX_QUERIES) |
| 2.6.2 | Anaphoric question ("What does it say?") with prior history | Standalone query resolves the pronoun |
| 2.6.3 | Chat history shown to rewrite = REWRITE_HISTORY_TURNS messages | Earlier turns beyond that limit omitted |
| 2.6.4 | Model returns more queries than REWRITE_MAX_QUERIES | Excess queries silently dropped |

### 2.7 Rerank

| # | Scenario | Expected |
|---|----------|----------|
| 2.7.1 | Passages up to RERANK_MAX_CANDIDATES submitted | At most RERANK_TOP_K kept afterwards |
| 2.7.2 | RERANK_SHORT_IDS=True | Model writes short IDs (RERANK_ID_CHARS chars) and they map back to full chunks |
| 2.7.3 | Comparison query with MODE=ALL | RERANK_COMPARE_MIN_PER_VERSION passages per version guaranteed |

### 2.8 Version detection

| # | Scenario | Expected |
|---|----------|----------|
| 2.8.1 | "Compare with previous version" | VersionScope includes older versions |
| 2.8.2 | Standard question | Only latest version chunks returned |
| 2.8.3 | VERSION_DETECTION_ENABLED=False | Every question uses latest only |

### 2.9 Suggested questions & user context

| # | Scenario | Expected |
|---|----------|----------|
| 2.9.1 | USER_CONTEXT_RECENT_QUESTIONS > 0 | Recent questions fetched and included in prompt |
| 2.9.2 | Recent-question DB read fails | Turn still succeeds; questions omitted silently |
| 2.9.3 | User has a `preferences` set on their profile | Preferences sent to model in answer prompt |

### 2.10 LLM model routing

| # | Scenario | Expected |
|---|----------|----------|
| 2.10.1 | LLM_HELPER_MODEL set | Rewrite, rerank, translate use that model; answer uses main |
| 2.10.2 | LLM_FALLBACK_MODEL set; main model errors before first token | Fallback model used for answer |
| 2.10.3 | Main model errors after first token has been streamed | Error event; fallback not used (too late) |
| 2.10.4 | LLM_TYPE=azure / litellm / ollama / aws / google | Correct client built; missing cred raises 500 at startup |

---

## 3. Chat History, Sessions & Feedback

### 3.1 History

| # | Scenario | Expected |
|---|----------|----------|
| 3.1.1 | GET `/chat/history` (no filters) | Paginated list; only caller's own turns |
| 3.1.2 | GET `/chat/history?session_id=X` | Only turns for that session |
| 3.1.3 | `page_size` > CHAT_PAGE_SIZE_MAX | 422 Unprocessable Entity |
| 3.1.4 | `page_number` = 0 | 422 Unprocessable Entity |

### 3.2 Sessions

| # | Scenario | Expected |
|---|----------|----------|
| 3.2.1 | GET `/chat/session` | Paginated list of caller's own sessions |
| 3.2.2 | PATCH `/chat/session/{id}` owned session | Session renamed |
| 3.2.3 | PATCH `/chat/session/{id}` not owned | 404 Not Found |
| 3.2.4 | DELETE `/chat/session/{id}` owned | 204 No Content; session soft-deleted |
| 3.2.5 | DELETE `/chat/session/{id}` not owned | 404 Not Found |

### 3.3 Feedback

| # | Scenario | Expected |
|---|----------|----------|
| 3.3.1 | POST `/chat/history/{id}/feedback` with thumbs-up | 200 "Feedback recorded." |
| 3.3.2 | Second submission for the same turn | Previous rating replaced (upsert) |
| 3.3.3 | history_id belonging to another user | 404 Not Found |
| 3.3.4 | Non-existent history_id | 404 Not Found |

### 3.4 User profile

| # | Scenario | Expected |
|---|----------|----------|
| 3.4.1 | GET `/chat/me` (DISABLE_AUTH=False) | Returns `display_name`, `preferences` |
| 3.4.2 | PATCH `/chat/me` with new `preferences` | Profile updated; returned object reflects change |
| 3.4.3 | PATCH `/chat/me` with only `display_name` | Only name changes; preferences unchanged |

---

## 4. File Management (Admin Only)

### 4.1 Upload

| # | Scenario | Expected |
|---|----------|----------|
| 4.1.1 | POST `/files` with a valid PDF | 201; file record created; queue message sent |
| 4.1.2 | Upload file with unsupported extension (e.g. `.exe`) | 415 Unsupported Media Type |
| 4.1.3 | Upload file > MAX_UPLOAD_SIZE_MB | 413 Request Entity Too Large |
| 4.1.4 | Upload duplicate content without `allow_duplicate=true` | 409 Conflict naming existing file |
| 4.1.5 | Upload duplicate with `allow_duplicate=true` | 201; audit row action=`file_duplicate` |
| 4.1.6 | Upload file whose name matches existing document (versioning) | New version stamped; version_no incremented |
| 4.1.7 | `version_label` not matching `VERSION_LABEL_PATTERN` | 422 Unprocessable Entity |
| 4.1.8 | `effective_date` in wrong format | 422 Unprocessable Entity |
| 4.1.9 | Storage backend upload fails | 500; no DB record created |
| 4.1.10 | Multiple files in one request | All processed sequentially; list returned |

### 4.2 Retrieve & download

| # | Scenario | Expected |
|---|----------|----------|
| 4.2.1 | GET `/files/{id}` existing file | 200 with full FileResponse |
| 4.2.2 | GET `/files/{id}` nonexistent | 404 Not Found |
| 4.2.3 | GET `/files/{id}/status` | 200 with processing_status only |
| 4.2.4 | GET `/files/{id}/download` | Streaming response with correct Content-Disposition |
| 4.2.5 | GET `/files/download-by-name?name=policy.pdf` (authenticated user, not admin) | 200 streaming (endpoint open to all authenticated users) |
| 4.2.6 | GET `/files/download-by-name?name=nonexistent.pdf` | 404 Not Found |
| 4.2.7 | GET `/files/` | List of all active file records |
| 4.2.8 | GET `/files/{id}/versions` | All versions of the document, newest first |

### 4.3 Delete & lifecycle

| # | Scenario | Expected |
|---|----------|----------|
| 4.3.1 | DELETE `/files/{integer_id}` | SQL record deleted; KB chunks removed; audit logged |
| 4.3.2 | DELETE `/files/{uuid}` with owning file record | Owning record + chunks deleted |
| 4.3.3 | DELETE `/files/{uuid}` KB-only document | Only vector chunks deleted |
| 4.3.4 | DELETE with invalid id (not int, not UUID) | 422 Unprocessable Entity |
| 4.3.5 | PATCH `/files/{id}/status` → DISABLE | Chunks moved to disabled collection; DB status updated |
| 4.3.6 | PATCH `/files/{id}/status` → ACTIVE | Chunks moved back to active collection |
| 4.3.7 | POST `/files/{id}/reindex` active file | Status set to PENDING; queue message sent |
| 4.3.8 | POST `/files/{id}/reindex` deleted file | 409 Conflict |

---

## 5. Rate Limiting

| # | Scenario | Expected |
|---|----------|----------|
| 5.1 | User sends exactly CHAT_RATE_LIMIT_PER_MINUTE turns within 60 s | Last allowed turn succeeds |
| 5.2 | User sends one more turn beyond the limit | 429 with `Retry-After` header |
| 5.3 | CHAT_RATE_LIMIT_PER_MINUTE=0 | No rate limit applied |
| 5.4 | Redis unreachable | Falls back to in-process counter; requests still rate-limited per replica |
| 5.5 | 60-second window elapses | Counter resets; user can send again |

---

## 6. Grounding Check

| # | Scenario | Expected |
|---|----------|----------|
| 6.1 | GROUNDING_CHECK_ENABLED=True; answer well-supported by citations | Verdict stored as `grounded` on history row |
| 6.2 | Answer contains a claim not supported by cited passage | Verdict stored as `ungrounded` |
| 6.3 | Grounding LLM call fails | Lexical fallback check runs; turn not failed |
| 6.4 | GROUNDING_CHECK_ENABLED=False | No grounding call made; verdict not set |
| 6.5 | Turn with no citations (evidence gate triggered) | Grounding check skipped |

---

## 7. Admin — Audit, Metrics & Trace

| # | Scenario | Expected |
|---|----------|----------|
| 7.1 | GET `/admin/audit` (no filter) | Paginated list of all audit events |
| 7.2 | GET `/admin/audit?action=file_upload` | Only upload events |
| 7.3 | GET `/admin/audit?actor=user@example.com` | Events by that user |
| 7.4 | GET `/admin/metrics?days=30` | Returns turn counts, error rate, latency, grounding, feedback stats |
| 7.5 | GET `/admin/chat-trace/{history_id}` | Full trace: queries, passages, outcome, model, config version |
| 7.6 | GET `/admin/chat-trace/{unknown_id}` | 404 Not Found |
| 7.7 | POST `/admin/purge` | Deletes audit rows older than AUDIT_RETENTION_DAYS, trace rows older than TRACE_RETENTION_DAYS |
| 7.8 | GET `/admin/audit` by non-admin | 403 Forbidden |

---

## 8. KB Queue Worker

### 8.1 Redis worker

| # | Scenario | Expected |
|---|----------|----------|
| 8.1.1 | File create message popped from queue | Parse → Index → DB status updated to `indexed` |
| 8.1.2 | Parse step fails (unsupported format at indexing layer) | File status set to `failed`; error stored |
| 8.1.3 | Index step fails (Mongo error) | File status set to `failed`; error stored |
| 8.1.4 | Worker pod crashes holding a lease | After WORKER_LEASE_SECONDS, another replica requeues the task |
| 8.1.5 | Task fails CHATBOT_QUEUE_MAX_RETRIES times | Moved to DLQ (CHATBOT_QUEUE_DLQ_NAME) |
| 8.1.6 | WORKER_BRPOP_TIMEOUT elapses with empty queue | Worker loops silently; no error |

### 8.2 Celery worker (QUEUE_TYPE=celery)

| # | Scenario | Expected |
|---|----------|----------|
| 8.2.1 | File task dispatched | Worker picks it up and runs parse/index |
| 8.2.2 | CELERY_CONCURRENCY files in flight simultaneously | All processed concurrently |
| 8.2.3 | Task not ack'd within CELERY_VISIBILITY_MARGIN_SECONDS | Redis redelivers to another worker |

### 8.3 Ingest recovery

| # | Scenario | Expected |
|---|----------|----------|
| 8.3.1 | File stuck in `pending` for > INGEST_RECOVERY_STUCK_SECONDS | Startup sweep requeues it |
| 8.3.2 | Periodic watchdog fires (INGEST_RECOVERY_INTERVAL_SECONDS) | Stuck files detected and requeued |
| 8.3.3 | INGEST_RECOVERY_INTERVAL_SECONDS=0 | Periodic check disabled; startup sweep still runs |

---

## 9. Parsing & Indexing Service

### 9.1 Document parsing

| # | Scenario | Expected |
|---|----------|----------|
| 9.1.1 | PDF submitted (text-based) | Text extracted and chunked |
| 9.1.2 | Scanned PDF (image pages) | Vision LLM called; text extracted from images |
| 9.1.3 | DOCX file | Text extracted from Word paragraphs |
| 9.1.4 | XLSX spreadsheet | Sheet data extracted as text |
| 9.1.5 | PNG / JPEG image | Vision LLM called |
| 9.1.6 | HTML file | HTML stripped; body text extracted |
| 9.1.7 | EML / MSG email | Headers + body extracted |
| 9.1.8 | Unsupported format slips past the upload check | Parsing handler returns structured error |

### 9.2 Chunking

| # | Scenario | Expected |
|---|----------|----------|
| 9.2.1 | Long document | Split into multiple overlapping chunks within token limit |
| 9.2.2 | Very short document (one paragraph) | Single chunk produced |
| 9.2.3 | Chunk size and overlap settings respected | Chunks do not exceed configured limit |

### 9.3 Embedding & indexing

| # | Scenario | Expected |
|---|----------|----------|
| 9.3.1 | EMBEDDING_TYPE=azure/litellm/remote | Correct client used; embeddings returned |
| 9.3.2 | Embedding endpoint rate-limited (429) | Retried up to EMBEDDING_MAX_RETRIES with backoff |
| 9.3.3 | Embedding request succeeds | Vectors stored in MongoDB with correct dimension (EMBEDDING_DIMENSION) |
| 9.3.4 | EMBEDDING_DIMENSION mismatch | Write fails with dimension error; not silently ignored |
| 9.3.5 | Batch size > EMBEDDING_BATCH_SIZE texts | Request split into batches automatically |
| 9.3.6 | Config check at startup with wrong/missing settings | Logged as error; service refuses to start |

### 9.4 Fusion & search (parsing service side)

| # | Scenario | Expected |
|---|----------|----------|
| 9.4.1 | Hybrid search (vector + BM25) | RRF fusion merges both ranked lists |
| 9.4.2 | LEXICAL_SEARCH=text | MongoDB `$text` index used instead of BM25 |
| 9.4.3 | Index health check at startup | Stale or missing index detected; warning logged |

---

## 10. Storage Backends

| # | Scenario | Expected |
|---|----------|----------|
| 10.1 | STORAGE_TYPE=azure | Files uploaded to / downloaded from Azure Blob Storage |
| 10.2 | STORAGE_TYPE=local (PVC) | Files read/written under LOCAL_STORAGE_PATH |
| 10.3 | STORAGE_TYPE=nextcloud | Files stored via WebDAV |
| 10.4 | STORAGE_TYPE=quay | Files pushed / pulled as OCI blobs |
| 10.5 | Citation linkify with `CHATBOT_BASE_URL` set | Links point to `/files/download-by-name` on this service |
| 10.6 | Citation linkify with `LOCAL_STORAGE_PUBLIC_URL` set | Links point to the public URL directly |
| 10.7 | Neither base URL set | Citation markers stay as plain text |
| 10.8 | SAS token expiry (Azure) | Download link includes valid SAS token; expired token rejected |

---

## 11. Database & Schema Migration

| # | Scenario | Expected |
|---|----------|----------|
| 11.1 | AUTO_APPLY_SCHEMA=True at first startup | All SQL scripts applied; tables created |
| 11.2 | AUTO_APPLY_SCHEMA=True; scripts already applied | Idempotent; no error |
| 11.3 | Two replicas start simultaneously | Migration lock prevents double-apply |
| 11.4 | Lock not released within SCHEMA_LOCK_TIMEOUT_SECONDS | Waiting instance stops with error |
| 11.5 | AUTO_APPLY_SCHEMA=False | No migration attempted at startup |
| 11.6 | MSSQL_POOL_SIZE concurrent DB calls | All served without deadlock |
| 11.7 | DB_TLS_CA_FILE set | TLS certificate verified against bundle |

---

## 12. Configuration Validation

| # | Scenario | Expected |
|---|----------|----------|
| 12.1 | LOG_LEVEL set to invalid string (e.g. `VERBOSE`) | Startup fails with clear validation error |
| 12.2 | GREETING_*_START_HOUR values not strictly ascending | Startup fails with clear validation error |
| 12.3 | Required secret (SECRET_KEY, REDIS_URL, DB_CONNECTION_STRING) missing | Startup fails immediately |
| 12.4 | CHAT_PAGE_SIZE_DEFAULT > CHAT_PAGE_SIZE_MAX | Request for max page returns 422 |
| 12.5 | TRANSLATION_MIN_RATIO check on a translation too short | Translation discarded; original answer used |

---

## 13. Observability

| # | Scenario | Expected |
|---|----------|----------|
| 13.1 | TRACE_ENABLED=True; chat turn completes | Row written to `chatbot_trace` |
| 13.2 | TRACE_ENABLED=False | No trace write attempted |
| 13.3 | Chat turn trace stored | GET `/admin/chat-trace/{id}` returns it in full |
| 13.4 | Startup + POST `/admin/purge` | Rows older than TRACE_RETENTION_DAYS / AUDIT_RETENTION_DAYS deleted |
| 13.5 | LOG_LEVEL=DEBUG | Per-turn debug lines emitted; not present at INFO |
| 13.6 | Lifespan LLM warm-up | `LLM_WARM_CONNECTIONS` connections established on startup |

---

## 14. Prompts & Language Handling

| # | Scenario | Expected |
|---|----------|----------|
| 14.1 | Arabic question | Language detected as Arabic; answer in Arabic |
| 14.2 | English question answered in English | No translation call made |
| 14.3 | Translation shorter than TRANSLATION_MIN_RATIO of original | Translation rejected; original English answer returned |
| 14.4 | LANGUAGE_SCRIPT_SHARE threshold | Mixed-script text not mis-labelled as single language |
| 14.5 | Greeting at hour=8 (morning) | "Good Morning" greeting variant used |
| 14.6 | Greeting at hour=14 (afternoon) | "Good Afternoon" greeting variant used |
| 14.7 | Greeting at hour=22 (night) | "Good Night" greeting variant used |

---

## 15. Frontend (chatbot.html)

| # | Scenario | Expected |
|---|----------|----------|
| 15.1 | Page loads | Chat input and send button rendered |
| 15.2 | Send a question | Streaming response appears token-by-token |
| 15.3 | `done` event received | Authoritative answer replaces streamed preview |
| 15.4 | Citation link in answer | Clicking opens the source document download |
| 15.5 | `error` event received | User-visible error message shown |
| 15.6 | New session (no conversation_id) | Session ID captured from `meta` event and reused for the session |
| 15.7 | Long conversation | Scroll position managed; old messages not lost |

---

## 16. End-to-End Scenarios

| # | Scenario | Expected |
|---|----------|----------|
| 16.1 | Admin uploads PDF → worker indexes it → user asks a question about it | Answer cites the PDF |
| 16.2 | Admin uploads a new version of an existing doc → user asks "compare versions" | Both versions retrieved and compared |
| 16.3 | Admin disables a file → user asks about it | Evidence gate triggers; no answer from disabled chunks |
| 16.4 | Admin re-enables the file → same question | Chunks back in search; answer returned |
| 16.5 | Admin deletes a file → catalog refreshed → user asks "what do you have?" | Deleted file no longer listed |
| 16.6 | User submits thumbs-up → admin checks metrics | Feedback count reflected in `/admin/metrics` |
| 16.7 | Turn with ungrounded citation → admin checks trace | Trace shows `ungrounded` verdict |
