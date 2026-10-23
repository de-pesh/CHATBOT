"""The database side of a feedback submission: params for upsert_chatbot_feedback /
filter_chatbot_feedback (scripts/mssql/06_chatbot_feedback.sql)."""

MAX_FEEDBACK_COMMENT_CHARS = 2000


def feedback_lookup_params(history_id: int) -> list:
    """filter_chatbot_feedback: the caller's existing feedback (if any) on this chat turn, so a
    second submission updates it instead of adding a duplicate row."""
    return [history_id]


def existing_feedback_id(result: list) -> int | None:
    """The id of the caller's existing feedback on a chat turn, or None for a first submission."""
    rows = result[0] if result else []
    return rows[0]["id"] if rows else None


def feedback_params(
    feedback_id: str | int | None, history_id: str | int, rating: str, comment: str | None, actor: str,
) -> list:
    """upsert_chatbot_feedback: [id, history_id, rating, comment, actor]."""
    return [feedback_id, history_id, rating, comment, actor]


def feedback_learning_context(feedbacks: list[dict] | None = None) -> str:
    """Aggregates user feedback signals into a learning context block for prompt refinement."""
    if not feedbacks:
        return ""
    positives = [f for f in feedbacks if f.get("rating") == "positive"]
    negatives = [f for f in feedbacks if f.get("rating") == "negative"]

    comments = [str(f.get("comment")).strip() for f in negatives if f.get("comment")]
    guidance = []
    if comments:
        guidance.append(f"Avoid issues noted in past user feedback: {'; '.join(comments[:3])}.")
    if len(positives) >= len(negatives) and len(positives) > 0:
        guidance.append("Maintain clear, direct, and well-structured answers.")

    if not guidance:
        return ""
    return "<learning_feedback_context>\n" + "\n".join(guidance) + "\n</learning_feedback_context>"
