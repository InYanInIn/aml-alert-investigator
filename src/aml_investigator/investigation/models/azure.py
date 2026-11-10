from collections.abc import Callable
from typing import Any, Literal

import httpx
from azure.identity import DefaultAzureCredential, get_bearer_token_provider
from langchain_core.language_models import LanguageModelInput
from langchain_core.runnables import Runnable
from langchain_openai import AzureChatOpenAI
from pydantic import BaseModel

AZURE_MODEL = "gpt-5-mini"
API_VERSION = "2025-04-01-preview"
TOKEN_SCOPE = "https://cognitiveservices.azure.com/.default"  # noqa: S105
REASONING_EFFORT = "high"
REQUEST_TIMEOUT_SECONDS = 600
UI_COMPLETION_LIMIT = 8192


class ToolCallingAzureChatOpenAI(AzureChatOpenAI):
    def with_structured_output(
        self,
        schema: dict[str, Any] | type | None = None,
        *,
        method: Literal["function_calling", "json_mode", "json_schema"] = "function_calling",
        include_raw: bool = False,
        strict: bool | None = None,
        **kwargs: Any,
    ) -> Runnable[LanguageModelInput, dict[str, Any] | BaseModel]:
        return super().with_structured_output(schema, method=method, include_raw=include_raw, strict=strict, **kwargs)


def azure_token_provider(*, guarded: bool = False) -> Callable[[], str]:
    provider = get_bearer_token_provider(DefaultAzureCredential(), TOKEN_SCOPE)
    if not guarded:
        return provider

    def token() -> str:
        try:
            return provider()
        except Exception:
            raise RuntimeError("Azure authentication failed.") from None

    return token


def azure_model(
    endpoint: str, *, sync_client: httpx.Client | None = None, async_client: httpx.AsyncClient | None = None
) -> ToolCallingAzureChatOpenAI:
    return ToolCallingAzureChatOpenAI(
        azure_endpoint=endpoint,
        azure_deployment=AZURE_MODEL,
        model=AZURE_MODEL,
        name=AZURE_MODEL,
        api_version=API_VERSION,
        api_key=None,
        azure_ad_token_provider=azure_token_provider(guarded=async_client is not None),
        reasoning_effort=REASONING_EFFORT,
        timeout=REQUEST_TIMEOUT_SECONDS,
        http_client=sync_client,
        http_async_client=async_client,
        max_completion_tokens=UI_COMPLETION_LIMIT if async_client is not None else None,
        max_retries=0 if async_client is not None else None,
        use_responses_api=False if async_client is not None else None,
    )
