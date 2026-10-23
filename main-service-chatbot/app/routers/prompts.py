"""Prompt Management API — reads/writes the pipeline's configurable system-instruction overrides.

GET  /prompts          - list all prompts (base template or active override)
GET  /prompts/{key}    - get one prompt's current text
PUT  /prompts/{key}    - admin: set a custom system-instruction override (takes effect on the next chat turn)
POST /prompts/{key}/reset - admin: remove the override, revert to the built-in template
"""
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from app.dependencies import require_admin
from app.logic.prompt_registry import (
    all_prompts,
    clear_override,
    get_prompt,
    set_override,
)

router = APIRouter()


class PromptItem(BaseModel):
    key: str
    name: str
    description: str
    template: str
    version: int
    has_override: bool = False


class PromptUpdate(BaseModel):
    template: str = Field(..., min_length=5, description="Custom system-instruction text that will be prepended to this prompt step.")
    description: str | None = Field(None, description="Ignored — descriptions are fixed per step.")


class PromptListResponse(BaseModel):
    total_records: int
    prompts: list[PromptItem]


@router.get(
    "",
    response_model=PromptListResponse,
    summary="List System Prompts",
    description="Lists all pipeline prompt steps with their current template (the built-in text, or an admin override if one is set).",
)
async def list_prompts() -> PromptListResponse:
    prompts = [PromptItem(**p) for p in all_prompts()]
    return PromptListResponse(total_records=len(prompts), prompts=prompts)


@router.get(
    "/{prompt_key}",
    response_model=PromptItem,
    summary="Get Prompt",
    description="Returns the current template for one prompt step.",
)
async def get_prompt_detail(prompt_key: str) -> PromptItem:
    item = get_prompt(prompt_key)
    if item is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Prompt '{prompt_key}' not found.")
    return PromptItem(**item)


@router.put(
    "/{prompt_key}",
    response_model=PromptItem,
    dependencies=[Depends(require_admin)],
    summary="Override System Prompt",
    description=(
        "Admin only. Sets a custom system instruction for this prompt step. "
        "It is prepended to the built-in prompt on every subsequent chat turn — no restart needed. "
        "Reset with POST /prompts/{key}/reset."
    ),
)
async def update_prompt(prompt_key: str, update: PromptUpdate) -> PromptItem:
    if get_prompt(prompt_key) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Prompt '{prompt_key}' not found.")
    version = set_override(prompt_key, update.template)
    item = get_prompt(prompt_key)
    item["version"] = version
    return PromptItem(**item)


@router.post(
    "/{prompt_key}/reset",
    response_model=PromptItem,
    dependencies=[Depends(require_admin)],
    summary="Reset Prompt Override",
    description="Admin only. Removes any custom override, reverting to the built-in template.",
)
async def reset_prompt(prompt_key: str) -> PromptItem:
    if get_prompt(prompt_key) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Prompt '{prompt_key}' not found.")
    clear_override(prompt_key)
    return PromptItem(**get_prompt(prompt_key))
