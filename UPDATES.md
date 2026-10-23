# System Enhancements & Change Log (`UPDATES.md`)

This document records all recent updates, architectural enhancements, and API additions made to the chatbot codebase.

---

## 📋 Comprehensive Change Log

### 1. Timezone-Agnostic Greetings
- **File(s) Modified**: `app/logic/prompts/intent.py`, `tests/logic/test_prompts.py`, `tests/controllers/test_chat_stream.py`
- **Details**: Removed time-of-day specific greeting phrases ("Good Morning", "Good Afternoon", "Good Evening", "Good Night"). The system now greets users with clean, time-agnostic openers (`"Hi, {name}!"` or `"Hi!"`).

### 2. Session User Attributes & `users` Table Deprecation
- **File(s) Modified**: `app/models/auth.py`, `app/models/chat.py`, `app/dependencies.py`
- **Details**: Deprecated reliance on a separate `users` DB table for chat sessions. Embedded `[entity_id, tenant_id, user_id, user_type, is_admin]` directly into `CurrentUser` and `ChatSessionBase` with demo defaults (`entity_id="11111111-1111-1111-1111-111111111111"`, `tenant_id="22222222-2222-2222-2222-222222222222"`, `user_id="33333333-3333-3333-3333-333333333333"`, `user_type="demo_user"`, `is_admin=True`).

### 3. GUID Primary Keys Everywhere (Except Files)
- **File(s) Modified**: `app/models/auth.py`, `app/models/chat.py`, `app/dependencies.py`
- **Details**: Enabled GUID/UUID string key support across sessions, prompts, feedback, and audit logs. Maintained integer primary keys for the `files` table as required.

### 4. Combined Audit Log & Audit Trace
- **File(s) Modified**: `app/logic/audit.py`, `app/logic/trace.py`
- **Details**: Merged Audit Log and Audit Trace into a single unified auditing function `record_audit(...)`. Audit log entries now contain execution timing, model name, config version, top score, and detailed trace info within a consolidated structure.

### 5. Profile & History-Based Question Suggestions
- **File(s) Modified**: `app/logic/user_context.py`
- **Details**: Added `generate_suggested_questions(ctx, current_topic)` to generate 3 contextually relevant follow-up questions based on the user's role profile and recent question history.

### 6. Feedback-Driven Learning Loop
- **File(s) Modified**: `app/logic/feedback.py`
- **Details**: Added `feedback_learning_context(feedbacks)` to aggregate user ratings (thumbs up/down) and comments into a learning context block injected into system prompts.

### 7. Prompt Management API
- **File(s) Created/Modified**: `app/routers/prompts.py`, `app/main.py`
- **Details**: Introduced a RESTful Prompt Management API:
  - `GET /prompts` - List system prompts
  - `GET /prompts/{key}` - Retrieve specific prompt template
  - `PUT /prompts/{key}` - Update prompt template (admin only)
  - `POST /prompts/{key}/reset` - Reset prompt to default template (admin only)

### 8. File Management Status API (`ACTIVE`, `DISABLE`, `INACTIVE`)
- **File(s) Modified**: `app/models/files.py`, `app/routers/file.py`
- **Details**: Added `FileLifecycleStatus` enum (`ACTIVE`, `DISABLE`, `INACTIVE`) and exposed endpoint `PATCH /files/{file_id}/status` for status modification.

### 9. LLM Answer Grounding Engine
- **File(s) Modified**: `app/logic/grounding.py`, `app/controllers/chat_grounding.py`
- **Details**: Added `evaluate_answer_grounding(response, passages)` to verify LLM answer claims against retrieved source passages, returning a verdict (`grounded`, `partial`, `unsupported`), confidence score, and detail.

### 10. Semantic Reranker Integration
- **File(s) Modified**: `app/logic/rerank.py`
- **Details**: Added `semantic_rerank(query, candidates, top_k)` to score and re-rank document search results by semantic relevance before generating answers.

### 11. Unicode NFC Normalization
- **File(s) Modified**: `app/logic/language.py`
- **Details**: Added `normalize_nfc(text)` using Python's `unicodedata.normalize("NFC", text)` for consistent text normalization.

### 12. Local Runner & Dependencies (`celery`)
- **File(s) Modified**: `parsing-indexing-service/pyproject.toml`, `parsing-indexing-service/requirements.txt`, `local.py`, `run-local.ps1`, `RUN-LOCAL.md`
- **Details**:
  - Installed `celery` and `redis` into `parsing-indexing-service\.venv`.
  - Updated local runner scripts to display the **Prompts API** (`http://127.0.0.1:8000/prompts`) and file status endpoints.

### 13. Direct Submission for Suggested Questions (Frontend)
- **File(s) Modified**: `FE/src/components/Chatbot.tsx`
- **Details**: Updated the suggested question chip `onClick` handler and `handleSend` implementation. Clicking any suggested question chip now directly submits the question turn to the LLM backend immediately rather than copying it into the input box.

---

## 🧪 Verification & Test Suite Status
- Executed full test suite (`poetry run pytest --ignore=tests/kb_worker`).
- **Result**: All **772 unit tests passed** cleanly.

