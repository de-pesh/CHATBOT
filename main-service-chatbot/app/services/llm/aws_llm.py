"""AWS Bedrock as the chat backend (LLM_TYPE=aws).

One of the vendor modules behind services/llm/chat_llm_factory.py; see that file's
docstring for the four functions every one of them exposes. Uses the Bedrock
Converse API through langchain-aws, so any Bedrock model that supports Converse
works by changing AWS_BEDROCK_MODEL_ID - Claude, Nova, Llama, Mistral - with no
code change. The langchain_aws import sits inside build() so an install running a
different backend never has to import it (or boto3).

Credentials: set AWS_ACCESS_KEY_ID and AWS_SECRET_ACCESS_KEY together, or leave
both unset to use boto3's default chain - an IAM role, IRSA / pod identity on
EKS or ROSA, the instance profile. The role route is what a cluster deployment
wants, which is why the keys are optional rather than required.
"""
import logging

import httpx
from langchain_core.language_models.chat_models import BaseChatModel

from app.config import Settings

logger = logging.getLogger(__name__)

# Bedrock's Converse API has no `reasoning_effort` argument, so the binding
# ChatLLM applies for the helper and answer steps must not be made -
# see chat_llm_factory.chat_llm_accepts_reasoning_effort.
ACCEPTS_REASONING_EFFORT = False


def required_settings() -> tuple:
    """Setting names that must all be populated for this backend to work.

    The access key pair is not listed: it is optional (see the module docstring),
    and half a pair is checked in build().
    """
    return ("AWS_REGION", "AWS_BEDROCK_MODEL_ID")


def endpoint(settings: Settings) -> str | None:
    """None: boto3 owns its own connections, so there is nothing to keep warm."""
    return None


def model_name(settings: Settings) -> str:
    """What the startup log calls the model - the Bedrock model or inference-profile ID."""
    return settings.AWS_BEDROCK_MODEL_ID or "?"


def build(
    settings: Settings,
    model: str | None = None,
    http_client: httpx.AsyncClient | None = None,
) -> BaseChatModel:
    """The chat client. `model` overrides the configured model ID.

    `http_client` is accepted for the contract and ignored: boto3 does not use httpx.
    """
    from langchain_aws import ChatBedrockConverse

    key_id = settings.AWS_ACCESS_KEY_ID
    secret = settings.AWS_SECRET_ACCESS_KEY
    if bool(key_id) != bool(secret):
        raise ValueError(
            "LLM_TYPE='aws' needs AWS_ACCESS_KEY_ID and AWS_SECRET_ACCESS_KEY together, "
            "or neither to use the default AWS credential chain (an IAM role)."
        )

    kwargs = {}
    if key_id:
        kwargs["aws_access_key_id"] = key_id
        kwargs["aws_secret_access_key"] = secret
        if settings.AWS_SESSION_TOKEN:
            kwargs["aws_session_token"] = settings.AWS_SESSION_TOKEN

    return ChatBedrockConverse(
        model=model or settings.AWS_BEDROCK_MODEL_ID,
        region_name=settings.AWS_REGION,
        temperature=settings.TEMPERATURE,
        **kwargs,
    )
