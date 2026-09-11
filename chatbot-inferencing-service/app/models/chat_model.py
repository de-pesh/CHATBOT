"""
This module defines Pydantic models for validating and structuring chat-related payloads.
"""
from pydantic import BaseModel, Field
from typing import List, Optional, Literal
from datetime import datetime

class Message(BaseModel):
    """Represents an individual chat message exchanged between user and AI.."""
    role: str
    content: str

class Chat(BaseModel):
    """Represents the payload for a chat request."""
    conversation_id: Optional[int] = Field(None, description="The ID of the current conversation session.")
    user_id: Optional[str] = None
    assessment_id: Optional[int] = Field(
        None,
        description="If provided, the chatbot answers about this specific assessment (its details, mapped evidence and run results) instead of general policies.",
    )
    framework_id: Optional[int] = Field(
        None,
        description="Framework ID of the active assessment; enables cross-referencing peer assessments under the same framework.",
    )
    chat: List[Message]


class ChatResult(BaseModel):
    """Outcome of processing a chat turn, including retrieval routing metadata."""
    response: Optional[str] = Field(None, description="The LLM-generated response text.")
    intent: str = Field("policy", description="Detected query intent: 'assessment' or 'policy'.")
    collections: List[str] = Field(
        default_factory=list,
        description="Vector collections that were searched to answer the query.",
    )

# --- Session Models ---

class ChatSessionBase(BaseModel):
    """Base model for chat session properties."""
    name: str = Field(..., description="The name or title of the chat session.")
    is_active: bool = True

class ChatSessionCreate(ChatSessionBase):
    """Model for creating a new chat session."""
    user_id: str

class ChatSessionUpdate(BaseModel):
    """Model for updating a session's name."""
    name: str = Field(..., description="The new name for the chat session.")
    user_id: str = Field(..., description="The ID of the user performing the update.")

class ChatSession(ChatSessionBase):
    """Represents a full chat session record from the database."""
    id: int
    created_on: datetime
    created_by: str
    updated_on: Optional[datetime]
    updated_by: Optional[str]

    class Config:
        from_attributes = True

class ChatSessionFilter(BaseModel):
    """Model for session filter query parameters."""
    id: Optional[int] = Field(None, description="Filter by session ID.")
    name: Optional[str] = Field(None, description="Filter by session name (supports partial match).")
    created_by: Optional[str] = Field(None, description="Filter by creator's identifier.")
    is_active: Optional[bool] = Field(True, description="Filter by active status.")

class PaginatedChatSessions(BaseModel):
    """Paginated response model for chat sessions."""
    total_records: int
    page_number: int
    page_size: int
    data: List[ChatSession]


# --- History Models (UPDATED) ---

class ChatHistoryFilter(BaseModel):
    id: Optional[int] = Field(None, description="Filter by chat history ID.")
    conversation_id: Optional[int] = Field(None, description="Filter by a specific conversation session ID.")
    prompt: Optional[str] = Field(None, description="Filter by prompt content (supports partial match).")
    response: Optional[str] = Field(None, description="Filter by response content (supports partial match).")
    feedback: Optional[Literal['positive', 'negative']] = Field(None, description="Filter by feedback.")
    created_by: Optional[str] = Field(None, description="Filter by creator's identifier.")
    is_active: Optional[bool] = Field(None, description="Filter by active status.")

class ChatHistoryRecord(BaseModel):
    id: int
    conversation_id: int
    prompt: str
    response: Optional[str]
    feedback: Optional[Literal['positive', 'negative']]
    error: Optional[str]
    comment: Optional[str]
    is_active: bool
    created_on: datetime
    created_by: str
    updated_on: Optional[datetime]
    updated_by: Optional[str]
    intent: Optional[str] = Field(
        None,
        description="Detected query intent for the turn ('assessment' or 'policy').",
    )
    retrieved_collections: Optional[str] = Field(
        None,
        description="Comma-separated vector collections the answer was retrieved from.",
    )

    class Config:
        from_attributes = True

class PaginatedChatHistory(BaseModel):
    total_records: int
    page_number: int
    page_size: int
    data: List[ChatHistoryRecord]