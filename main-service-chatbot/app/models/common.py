"""Common models."""

from pydantic import BaseModel, Field


# --- from generic_models.py ---
class StatusResponse(BaseModel):
    status: str = Field(..., description="The status message.")


# --- from error_models.py ---
class ErrorResponse(BaseModel):
    '''
    Represents a standardized error response model.

    Attributes:
        detail (str): A descriptive message detailing the error.
        code (Optional[str]): An optional, programmatic error code.
    '''
    detail: str = Field(..., description="A descriptive message detailing the error.")
    code: str | None = Field(None, description="An optional, programmatic error code.")
