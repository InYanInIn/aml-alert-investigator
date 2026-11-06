from typing import Any, Literal

from azure.identity import DefaultAzureCredential, get_bearer_token_provider
from langchain_core.language_models import LanguageModelInput
from langchain_core.runnables import Runnable
from langchain_openai import AzureChatOpenAI
from pydantic import BaseModel

AZURE_MODEL = "gpt-5-mini"
API_VERSION = "2025-04-01-preview"
TOKEN_SCOPE = "https://cognitiveservices.azure.com/.default"  # noqa: S105
REASONING_EFFORT = "medium"


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


def azure_model(endpoint: str) -> ToolCallingAzureChatOpenAI:
    return ToolCallingAzureChatOpenAI(
        azure_endpoint=endpoint,
        azure_deployment=AZURE_MODEL,
        model=AZURE_MODEL,
        name=AZURE_MODEL,
        api_version=API_VERSION,
        api_key=None,
        azure_ad_token_provider=get_bearer_token_provider(DefaultAzureCredential(), TOKEN_SCOPE),
        reasoning_effort=REASONING_EFFORT,
    )
