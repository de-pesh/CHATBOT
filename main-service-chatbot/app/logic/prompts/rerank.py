"""Step 3 of a chat turn: the prompt that orders the retrieved passages."""

import json


def fetch_rerank_prompt(
    query_text: str, candidates: list, top_k: int, id_chars: int, passage_chars: int,
    short_ids: bool = False,
):
    """
    Constructs a prompt asking the model to reorder retrieved passages by
    relevance to the original query, dropping any that don't belong.

    Hybrid search (vector + lexical, RRF-fused) optimises for recall, and
    query expansion (see fetch_new_query_prompt) widens the candidate pool
    further on purpose - several search queries, each returning its own hits,
    merged and deduplicated before this is ever called. That trade needs a
    precision pass afterwards or the extra recall just becomes extra noise in
    the final prompt; this is that pass.

    - query_text is the original user query, not a rephrased/expanded variant.
    - short_ids asks for only the first id_chars characters of each id in the
      reply. An id is a 36-character UUID, and writing up to top_k of those out
      is most of what the model spends its time on. What it reads is unchanged;
      only its reply is shorter. rerank.reorder_by_ids maps either kind of reply
      back, so the caller need not know which was asked.
    - id_chars and passage_chars are Settings.RERANK_ID_CHARS / RERANK_PASSAGE_CHARS:
      the reply's id length and how much of each candidate's text the model is shown.
    """
    reply_line = (
        f" Write each id as only its first {id_chars} characters." if short_ids else ""
    )
    example = (
        f'"{"1a2b3c4d5e6f"[:id_chars]}", "{"7a8b9c0d1e2f"[:id_chars]}"' if short_ids
        else '"id1", "id2"'
    )
    # Each field JSON-encoded: passage text is document text, and one quote or
    # newline in it would otherwise run two passages together, or split one into
    # a line that reads like a further passage with an id of its own.
    passages = "\n".join(
        "- id: {} | content: {}".format(
            json.dumps(str(c.get("id") or ""), ensure_ascii=False),
            json.dumps((c.get("metadata") or {}).get("textContent", "")[:passage_chars], ensure_ascii=False),
        )
        for c in candidates
    )

    rerank_prompt = f"""You are ranking retrieved passages by how relevant they are to a user's question.

    Passages:
    {passages}

    Question: {query_text}

    Return the ids of the passages that actually help answer the question,
    ordered from most to least relevant. Leave out any passage that is not
    relevant, even if that means returning fewer than {top_k} ids. Return at
    most {top_k} ids.{reply_line}

    Output strictly in JSON like:
    ```json
    {{"ranked_ids": [{example}]}}
    ```"""
    return [{"role": "user", "content": rerank_prompt}]
