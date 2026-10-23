# Chat Flow

How a user message goes from the browser → streamed answer with citations.

---

## Overview

```
Browser  →  POST /chat/stream (SSE)
                      ↓
            main-service-chatbot
                      ↓
        ┌─────────────────────────┐
        │   process_chat_stream   │
        │  ┌─────────────────┐    │
        │  │ rewrite (async) │    │
        │  │ check_intent    │    │ (run together)
        │  └─────────────────┘    │
        │   retrieve (vector DB)  │
        │   rerank (LLM call)     │
        │   stream_answer (LLM)   │
        │   [translate if needed] │
        └─────────────────────────┘
                      ↓
            Browser assembles SSE deltas into the final answer
```

---

## Step 0 — Auth + session bookkeeping

**Router:** [main-service-chatbot/app/routers/chat.py](main-service-chatbot/app/routers/chat.py)  
**Controller:** [main-service-chatbot/app/controllers/chat_controller.py](main-service-chatbot/app/controllers/chat_controller.py) `ChatSessionController`

Before the pipeline runs:

1. JWT validated by `security.py`.
2. `ensure_session` — if `conversation_id` is omitted, creates a new session row (`UPSERT_CHATBOT_SESSION`); if provided, verifies it belongs to the caller (prevents session hijacking).
3. `insert_prompt` — writes the incoming question as a history row (`UPSERT_CHATBOT_HISTORY`) → returns `history_id`.

---

## Step 1 — Greeting shortcut

**[chat_controller.py:201](main-service-chatbot/app/controllers/chat_controller.py#L201)**

If `INTENT_ROUTING_ENABLED` is on and the message is a bare "hi"/"hey"/"hello", the reply is assembled **without any model call** and yielded immediately. This is the most common message; handling it inline means it's instant even if the intent-classification call is down.

---

## Step 2 — Rewrite + Intent check (run in parallel)

**[chat_steps.py:255](main-service-chatbot/app/controllers/chat_steps.py#L255)** `ChatTurn.rewrite`  
**[chat_steps.py:236](main-service-chatbot/app/controllers/chat_steps.py#L236)** `ChatTurn.check_intent`

Both start as `asyncio.Task`s at the same time ([chat_controller.py:213](main-service-chatbot/app/controllers/chat_controller.py#L213)).

**Rewrite** calls the helper LLM with the conversation history to produce:
- A standalone version of the question (for embedding, strips context-dependent references like "that document").
- Up to `REWRITE_EXPANSION_BUDGET` extra queries (query expansion for broader recall).
- The language of the question.

**Intent check** classifies the message:
- `chitchat` / small-talk → answers directly without searching the knowledge base.
- A question naming a specific document from the KB overview → answers with a description, no search.
- Everything else → `None` (continue to search).

If the intent check finishes first and calls it small-talk, the pipeline returns early.  
If the rewrite finishes first, search starts immediately; the intent check gate is checked before generation begins.

---

## Step 3 — Retrieve

**[chat_steps.py:276](main-service-chatbot/app/controllers/chat_steps.py#L276)** `ChatTurn.retrieve`

Fans out one vector search per query (original + expanded queries) with `asyncio.gather`.  
Each search calls `fetch_context` → `SearchRuntime` → MongoDB vector search.

Results are fused with **Reciprocal Rank Fusion** (RRF) across the per-query hit lists ([chat_steps.py:310](main-service-chatbot/app/controllers/chat_steps.py#L310)).  
Hits below `MIN_RELEVANCE_SCORE` are dropped before fusion.

Version scope (`VERSION_DETECTION_ENABLED`): if the question asks about an older version of a document, the search scope is narrowed to that version.

---

## Step 4 — Rerank

**[chat_steps.py:314](main-service-chatbot/app/controllers/chat_steps.py#L314)** `ChatTurn.rerank`

One extra LLM call that reorders the fused hit list by relevance to the original question and drops off-topic passages.  
Falls back to score order if the call fails — a degraded ranking, never no answer.

---

## Step 5 — No-evidence gate

**[chat_controller.py:257](main-service-chatbot/app/controllers/chat_controller.py#L257)**

If `NO_EVIDENCE_GATE_ENABLED` and no passages survived, the `NO_EVIDENCE_REPLY` is sent (translated to the question's language if needed) rather than asking the model to fabricate an answer from nothing.

---

## Step 6 — Stream answer

**[chat_steps.py:375](main-service-chatbot/app/controllers/chat_steps.py#L375)** `ChatTurn.stream_answer`

Sources (cited passage metadata) are yielded to the browser **before** generation starts, so the UI can pre-load download links while the answer is being written.

`stream_llm` yields tokens from the LLM as they arrive; the controller yields each as a `delta` SSE event.

After the full answer is assembled:
- **Wrong-script check**: if the answer came out in the wrong language, it's regenerated once with the language spelled out explicitly.
- **Translation**: if the question was in a non-English script, the full answer is translated.

---

## Step 7 — Save + linkify

Back in the router:
1. `record_success` — writes the answer to the `chatbot_history` row created in step 0.
2. Citations in the answer (`[1]`, `[2]` etc.) are turned into download links (`linkify`).
3. The final `done` SSE event is sent with the authoritative `response`, `history_id`, per-stage `timings`, and optional `suggested_questions`.

**Note:** The text accumulated from `delta` events is a live preview. The `done.response` field is authoritative (may have been translated or had links inserted after streaming).

---

## Browser (Frontend)

**[FE/src/hooks/useChatStream.ts](FE/src/hooks/useChatStream.ts)**

The browser cannot use `EventSource` (GET only, no body), so the frontend posts with `fetch()` and parses SSE frames off the `ReadableStream` by hand.

SSE event types dispatched to the UI:

| Event | Meaning |
|---|---|
| `meta` | `conversation_id` — which session this turn was logged against |
| `stage` | `rephrasing` / `retrieving` / `reranking` / `generating` / `finalising` |
| `delta` | One text chunk — append to the in-progress answer |
| `done` | Authoritative final answer; replace the accumulated deltas with this |
| `error` | Turn failed; `reason: "failed"` or `"interrupted"` |

**[FE/src/components/Chatbot.tsx](FE/src/components/Chatbot.tsx)** — main chat UI component  
**[FE/src/components/FloatingChat.tsx](FE/src/components/FloatingChat.tsx)** — embeddable floating widget

---

## Helper model vs. main model

**[chat_steps.py:91](main-service-chatbot/app/controllers/chat_steps.py#L91)** `helper_json`

The rewrite, rerank, and translate steps use a lighter "helper" model if `CHAT_LLM_HELPER_MODEL` is configured.  
If the helper returns unusable JSON, the call is automatically retried on the main model.  
The main model's failure is also checked — if it too returns unusable output, the function returns `None` and the step falls back gracefully (e.g. rerank falls back to score order).

---

## Key services

| Interface | Role |
|---|---|
| `ChatLLM` | Abstraction over Azure OpenAI / AWS Bedrock / Google / LiteLLM / Ollama |
| `SearchRuntime` | Wraps the MongoDB vector store + lexical hybrid search |
| `DatabaseService` | MSSQL via pyodbc (sessions, history, audit, file records) |
