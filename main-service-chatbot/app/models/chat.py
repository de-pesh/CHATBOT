"""
This module defines Pydantic models for validating and structuring chat-related payloads.
"""
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from app.logic.feedback import MAX_FEEDBACK_COMMENT_CHARS
from app.logic.user_context import (
    MAX_DISPLAY_NAME_CHARS,
    MAX_PREFERENCE_KEY_CHARS,
    MAX_PREFERENCE_VALUE_CHARS,
    MAX_PREFERENCES,
)


class Message(BaseModel):
    """Represents an individual chat message exchanged between user and AI.."""
    role: str
    content: str

class Chat(BaseModel):
    """Represents the payload for a chat request."""
    conversation_id: str | int | None = Field(None, description="The ID (GUID or integer) of the current conversation session.")
    chat: list[Message]
    local_hour: int | None = Field(
        None, ge=0, le=23,
        description="The user's local hour (0-23).",
    )


class ChatResult(BaseModel):
    """Outcome of processing a chat turn, including retrieval routing metadata."""
    response: str | None = Field(None, description="The LLM-generated response text.")
    collections: list[str] = Field(
        default_factory=list,
        description="Vector collections that were searched to answer the query.",
    )
    sources: list[dict] = Field(
        default_factory=list,
        description="The numbered passages the answer was generated from: "
                    "{n, file_name, page}. A [n] marker in the response refers to entry n.",
    )
    passages: dict[int, str] = Field(
        default_factory=dict, exclude=True,
        description="n -> the cited passage's full text (app.logic.grounding.cited_passage_texts), "
                    "numbered the same way `sources` is. Server-side only, for the after-the-fact "
                    "grounding check (app/controllers/chat_grounding.py) - never serialized to the client.",
    )
    trace: dict = Field(
        default_factory=dict, exclude=True,
        description="What the turn did (app.logic.trace): queries, retrieved and reranked passages. "
                    "Server-side only, stored in dbo.chatbot_trace - never serialized to the client.",
    )
    no_evidence: bool = Field(
        False, exclude=True,
        description="True when the evidence gate answered because the search found nothing usable.",
    )
    intent: str | None = Field(
        None,
        description="What the intent step decided: 'chitchat' or 'kb_overview' when the reply was "
                    "written without searching the knowledge base, else None (the full pipeline ran).",
    )
    timings: dict[str, int] = Field(
        default_factory=dict,
        description="Milliseconds spent per pipeline stage: rephrase_ms, retrieve_ms, rerank_ms, "
                    "first_token_ms, generate_ms, translate_ms (only when it ran), pipeline_ms.",
    )

# --- Session Models ---

class ChatSessionBase(BaseModel):
    """Base model for chat session properties."""
    name: str = Field(..., description="The name or title of the chat session.")
    entity_id: str = Field("11111111-1111-1111-1111-111111111111", description="Entity GUID")
    tenant_id: str = Field("22222222-2222-2222-2222-222222222222", description="Tenant GUID")
    user_id: str = Field("33333333-3333-3333-3333-333333333333", description="User GUID")
    user_type: str = Field("demo_user", description="Type of user")
    is_admin: bool = Field(True, description="Is admin user")
    is_active: bool = True

class ChatSessionUpdate(BaseModel):
    """Model for updating a session's name."""
    name: str = Field(..., description="The new name for the chat session.")

class ChatSession(ChatSessionBase):
    """Represents a full chat session record from the database."""
    id: str | int
    created_on: datetime
    created_by: str
    updated_on: datetime | None = None
    updated_by: str | None = None

class ChatSessionFilter(BaseModel):
    """Model for session filter query parameters."""
    id: str | int | None = Field(None, description="Filter by session ID.")
    name: str | None = Field(None, description="Filter by session name (supports partial match).")
    is_active: bool | None = Field(True, description="Filter by active status.")

class PaginatedChatSessions(BaseModel):
    """Paginated response model for chat sessions."""
    total_records: int
    page_number: int
    page_size: int
    data: list[ChatSession]


# --- History Models (UPDATED) ---

class ChatHistoryFilter(BaseModel):
    """Model for history filter query parameters."""
    id: int | str | None = Field(None, description="Filter by chat history ID.")
    conversation_id: int | str | None = Field(None, description="Filter by a specific conversation session ID.")
    prompt: str | None = Field(None, description="Filter by prompt content (supports partial match).")
    response: str | None = Field(None, description="Filter by response content (supports partial match).")
    is_active: bool | None = Field(None, description="Filter by active status.")

    @field_validator("id", "conversation_id", mode="before")
    @classmethod
    def _parse_int_if_numeric(cls, value):
        if isinstance(value, str) and value.isdigit():
            return int(value)
        return value

class ChatHistoryRecord(BaseModel):
    id: int | str
    conversation_id: int | str
    prompt: str
    response: str | None
    error: str | None
    is_active: bool
    created_on: datetime
    created_by: str
    updated_on: datetime | None = None
    updated_by: str | None = None
    rag_collections: str | None = Field(
        None,
        description="Comma-separated vector collections the answer was retrieved from.",
    )
    grounding_verdict: Literal['grounded', 'partial', 'unsupported'] | None = Field(
        None,
        description="Whether this answer's citations were actually supported by what they cite.",
    )
    grounding_detail: str | None = Field(
        None, description="One short sentence on what was unsupported, for a 'partial' or 'unsupported' verdict.",
    )

class PaginatedChatHistory(BaseModel):
    total_records: int
    page_number: int
    page_size: int
    data: list[ChatHistoryRecord]


# --- Feedback (POST /chat/history/{history_id}/feedback) ---

class ChatFeedbackCreate(BaseModel):
    """A thumbs up/down on one answer."""
    rating: Literal['positive', 'negative'] = Field(..., description="Thumbs up ('positive') or down ('negative').")
    comment: str | None = Field(
        None, max_length=MAX_FEEDBACK_COMMENT_CHARS,
        description="Optional detail. Typically given alongside a 'negative' rating.",
    )


# --- The caller's chat profile (GET / PATCH /chat/me) ---

class ChatUserProfile(BaseModel):
    """Who the caller is to the chatbot, and what its prompts may know about them."""
    id: int | str
    email: str
    display_name: str | None = Field(None, description="How the assistant addresses the user.")
    preferences: dict[str, str] = Field(
        default_factory=dict,
        description="Short facts the assistant may use to fit its answers to the user, e.g. {'role': 'Compliance officer'}.",
    )


class ChatUserUpdate(BaseModel):
    """The profile fields a user can change. A field left out is left as it is."""
    display_name: str | None = Field(
        None, max_length=MAX_DISPLAY_NAME_CHARS, description="An empty string clears the name.",
    )
    preferences: dict[str, str | int | float] | None = Field(
        None,
        description=f"Replaces all preferences: at most {MAX_PREFERENCES} entries, keys up to "
                    f"{MAX_PREFERENCE_KEY_CHARS} characters and values up to {MAX_PREFERENCE_VALUE_CHARS}. "
                    "An empty object clears them.",
    )

    @field_validator("preferences")
    @classmethod
    def _within_the_limits(cls, preferences):
        if preferences is None:
            return preferences
        if len(preferences) > MAX_PREFERENCES:
            raise ValueError(f"at most {MAX_PREFERENCES} preferences")
        for key, value in preferences.items():
            if len(str(key)) > MAX_PREFERENCE_KEY_CHARS or len(str(value)) > MAX_PREFERENCE_VALUE_CHARS:
                raise ValueError(
                    f"a preference key is at most {MAX_PREFERENCE_KEY_CHARS} characters and a value at most {MAX_PREFERENCE_VALUE_CHARS}",
                )
        return preferences

    @model_validator(mode="after")
    def _says_something(self):
        if self.display_name is None and self.preferences is None:
            raise ValueError("send display_name, preferences or both")
        return self
