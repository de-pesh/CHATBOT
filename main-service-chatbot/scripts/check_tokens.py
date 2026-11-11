"""
Token usage verification script — real LLM calls with REAL chunks from MongoDB.

Run this from inside the OpenShift pod where env vars are already injected:

    oc cp scripts/check_tokens.py <pod>:/tmp/check_tokens.py
    oc exec -it <pod> -- python /tmp/check_tokens.py

It reads LITELLM_* and MONGO_* env vars already present in the pod.
No app code imported, no server started.
"""

import json
import os
import sys

try:
    import requests
except ImportError:
    sys.exit("requests is not installed.")


# ---------------------------------------------------------------------------
# Config — env vars already present in the pod
# ---------------------------------------------------------------------------

BASE_URL       = os.environ.get("LITELLM_BASE_URL",        "").rstrip("/")
API_KEY        = os.environ.get("LITELLM_API_KEY",         "")
MODEL_NAME     = os.environ.get("LITELLM_MODEL_NAME",      "")
FALLBACK_MODEL = os.environ.get("LLM_FALLBACK_MODEL",      "glm-5")
MONGO_URI      = os.environ.get("MONGO_CONNECTION_STRING",  "")
MONGO_DB       = os.environ.get("MONGO_DATABASE_NAME",      "chatbot")
MONGO_COLL     = os.environ.get("MONGODB_COLLECTION_NAME",  "chatbot_policies")

RERANK_TOP_K         = 8
RERANK_MAX_CANDS     = 20
RERANK_PASSAGE_CHARS = 600

if not all([BASE_URL, API_KEY, MODEL_NAME]):
    sys.exit("Set LITELLM_BASE_URL, LITELLM_API_KEY, LITELLM_MODEL_NAME")

SAMPLE_QUESTION = "What are the requirements for cii incident reporting?"


# ---------------------------------------------------------------------------
# Pull real chunks from MongoDB
# ---------------------------------------------------------------------------

def fetch_real_chunks(limit: int) -> list:
    try:
        from pymongo import MongoClient
    except ImportError:
        return []
    if not MONGO_URI:
        return []
    try:
        client = MongoClient(MONGO_URI, serverSelectionTimeoutMS=5000)
        coll   = client[MONGO_DB][MONGO_COLL]
        docs   = list(coll.aggregate([{"$sample": {"size": limit}}]))
        client.close()
        chunks = []
        for doc in docs:
            text = (
                doc.get("textContent") or
                (doc.get("metadata") or {}).get("textContent") or
                str(doc.get("text") or "")
            )
            chunks.append({"id": str(doc.get("_id", f"chunk-{len(chunks):04d}")), "content": text})
        return chunks
    except Exception as e:
        print(f"  [MongoDB error: {e}]")
        return []


def build_data():
    print("  Fetching real chunks from MongoDB...", end="", flush=True)
    chunks = fetch_real_chunks(RERANK_MAX_CANDS)

    if chunks:
        avg = int(sum(len(c["content"]) for c in chunks) / len(chunks))
        print(f" {len(chunks)} chunks, avg {avg} chars")
    else:
        # Realistic fallback matching DESC document style (tables + regulatory text)
        print(" MongoDB unavailable — using realistic fallback")
        body = (
            "Critical Information Infrastructure (CII) organizations shall establish, implement, "
            "and maintain a comprehensive risk management process aligned with this methodology. "
            "The process shall encompass risk identification, assessment, treatment, monitoring, "
            "and review. Risk assessments shall be conducted at least annually and upon significant "
            "changes to the CII environment. All identified risks shall be documented in a risk "
            "register. Treatment plans shall include specific controls, timelines, responsible "
            "parties, and residual risk acceptance criteria. The Board shall be informed of critical "
            "risks quarterly. Evidence of risk treatment shall be retained for a minimum of five "
            "years. Organizations shall ensure that third-party and supply chain risks are addressed "
            "within the risk framework. Version 1.1 | Refined | Dubai Electronic Security Centre. "
            "Document Classification: Internal / Restricted. Related Documents: CII Framework; "
            "CII Identification Methodology; Critical Service Identification Methodology. "
            "Approval Authority: DESC Board of Directors. Next Scheduled Review: annually."
        )
        chunks = [{"id": f"chunk-{i:04d}", "content": body} for i in range(RERANK_MAX_CANDS)]

    answer_passages = "\n".join(
        f"[{i+1}] {c['content']}" for i, c in enumerate(chunks[:RERANK_TOP_K])
    )
    rerank_candidates = "\n".join(
        f'- id: {json.dumps(c["id"])} | content: {json.dumps(c["content"][:RERANK_PASSAGE_CHARS])}'
        for c in chunks
    )
    history = json.dumps([
        {"role": "user",      "content": "Can you give me an overview of the CII framework?"},
        {"role": "assistant", "content": "The CII framework establishes minimum requirements for critical information infrastructure protection in Dubai [1]."},
    ])
    return answer_passages, rerank_candidates, history


# ---------------------------------------------------------------------------
# Prompt builders — simplified but accurate since real MongoDB chunks are used
# ---------------------------------------------------------------------------

def answer_messages(chunks, history_str):
    hist = json.loads(history_str)
    passages = "\n".join(f"[{i+1}] {c['content']}" for i, c in enumerate(chunks[:RERANK_TOP_K]))
    system = (
        "You are a knowledgeable assistant for Dubai Electronic Security Centre (DESC). "
        "Answer based only on the provided context. If the answer is not in the context, "
        "say you don't have that information. Cite sources using [n] notation. "
        "Be concise, accurate, and professional. Do not make up information. "
        "Always respond in the same language as the user's question."
    )
    return [
        {"role": "system",    "content": system},
        {"role": "user",      "content": hist[0]["content"]},
        {"role": "assistant", "content": hist[1]["content"]},
        {"role": "user",      "content": f"Context:\n{passages}\n\nQuestion: {SAMPLE_QUESTION}"},
    ]


def rewrite_messages(history_str):
    hist = json.loads(history_str)
    history_text = "\n".join(f"{m['role']}: {m['content']}" for m in hist)
    return [{"role": "user", "content": (
        f"Rewrite the following query into 4 search-optimized variants. "
        f"Detect language and return JSON with keys: language, language_code, questions (array of {{query, type}}).\n\n"
        f"Chat history:\n{history_text}\n\nQuery: {SAMPLE_QUESTION}"
    )}]


def rerank_messages(chunks):
    lines = "\n".join(
        f'- id: {json.dumps(c["id"][:8])} | content: {json.dumps(c["content"][:RERANK_PASSAGE_CHARS])}'
        for c in chunks
    )
    return [{"role": "user", "content": (
        f"Rank these {len(chunks)} passages by relevance to the query. "
        f"Return JSON array of the top {RERANK_TOP_K} passage IDs only.\n\n"
        f"Query: {SAMPLE_QUESTION}\n\nPassages:\n{lines}"
    )}]


def intent_messages(history_str):
    hist = json.loads(history_str)
    history_text = "\n".join(f"{m['role']}: {m['content']}" for m in hist[-3:])
    return [{"role": "user", "content": (
        f"Classify this message as 'chitchat' or 'knowledge'. "
        f"Return JSON: {{intent, language, reply}}. "
        f"If chitchat, provide a friendly reply; if knowledge, leave reply empty.\n\n"
        f"History:\n{history_text}\n\nMessage: {SAMPLE_QUESTION}"
    )}]


# ---------------------------------------------------------------------------
# LLM call
# ---------------------------------------------------------------------------

def call_llm(label: str, messages: list) -> dict:
    url     = f"{BASE_URL}/chat/completions"
    headers = {"Authorization": f"Bearer {API_KEY}", "Content-Type": "application/json"}
    for model in [MODEL_NAME, FALLBACK_MODEL]:
        if not model:
            continue
        body = {"model": model, "messages": messages, "stream": False}
        try:
            resp = requests.post(url, headers=headers, json=body, timeout=90)
            resp.raise_for_status()
            data    = resp.json()
            usage   = data.get("usage", {})
            content = data.get("choices", [{}])[0].get("message", {}).get("content", "")
            used_model = f" [{model}]" if model != MODEL_NAME else ""
            return {
                "label":   label + used_model,
                "input":   usage.get("prompt_tokens",     0),
                "output":  usage.get("completion_tokens", 0),
                "total":   usage.get("total_tokens",      0),
                "content": content,
                "error":   None,
            }
        except Exception as exc:
            if model == MODEL_NAME and FALLBACK_MODEL:
                print(f" (primary 503, trying fallback {FALLBACK_MODEL}...", end="", flush=True)
                continue
            return {"label": label, "input": 0, "output": 0, "total": 0, "content": "", "error": str(exc)}
    return {"label": label, "input": 0, "output": 0, "total": 0, "content": "", "error": "all models failed"}


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    print(f"\nEndpoint : {BASE_URL}")
    print(f"Model    : {MODEL_NAME}")
    print(f"Question : {SAMPLE_QUESTION}")
    print("-" * 70)

    _, _, history = build_data()
    # re-fetch so we have the raw chunk list for prompt builders
    chunks = fetch_real_chunks(RERANK_MAX_CANDS)
    if not chunks:
        body = (
            "Critical Information Infrastructure (CII) organizations shall establish and maintain "
            "a comprehensive risk management process. Risk assessments shall be conducted annually. "
            "All identified risks shall be documented in a risk register. Treatment plans shall "
            "include controls, timelines, and residual risk acceptance criteria approved by the Board."
        )
        chunks = [{"id": f"chunk-{i:04d}", "content": body} for i in range(RERANK_MAX_CANDS)]

    calls = [
        ("Intent check",  intent_messages(history)),
        ("Query rewrite", rewrite_messages(history)),
        ("Rerank",        rerank_messages(chunks)),
        ("Answer (main)", answer_messages(chunks, history)),
    ]

    results = []
    for label, messages in calls:
        print(f"  Calling {label}...", end="", flush=True)
        r = call_llm(label, messages)
        results.append(r)
        print(f" {'ERROR: ' + r['error'] if r['error'] else str(r['total']) + ' tokens'}")

    print("\n" + "=" * 70)
    print(f"{'Call':<20} {'Input':>8} {'Output':>8} {'Total':>8}")
    print("-" * 70)

    total_in = total_out = 0
    for r in results:
        if r["error"]:
            print(f"  {r['label']:<18} {'ERROR':>8}  {r['error'][:50]}")
        else:
            print(f"  {r['label']:<18} {r['input']:>8,} {r['output']:>8,} {r['total']:>8,}")
            total_in  += r["input"]
            total_out += r["output"]

    print("-" * 70)
    grand = total_in + total_out
    print(f"  {'TOTAL / TURN':<18} {total_in:>8,} {total_out:>8,} {grand:>8,}")
    print(f"  {'Input %':<18} {round(total_in/grand*100) if grand else 0:>7}%")
    print(f"  {'Output %':<18} {round(total_out/grand*100) if grand else 0:>7}%")
    print("=" * 70)

    # --- full output for each call ---
    print("\n\nRESPONSES\n" + "=" * 70)
    for r in results:
        print(f"\n── {r['label']} ──")
        if r["error"]:
            print(f"  ERROR: {r['error']}")
        elif r["content"]:
            print(r["content"])
        else:
            print("  (empty)")
        print()

    if grand:
        print("=" * 70)
        print(f"20 concurrent users × 1 turn  → {grand * 20:>10,} tokens")
        print(f"20 concurrent users × 5 turns → {grand * 20 * 5:>10,} tokens")
    print()


if __name__ == "__main__":
    main()
