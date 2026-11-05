from typing import Any, Literal

from langchain_core.language_models import LanguageModelInput
from langchain_core.runnables import Runnable
from langchain_ollama import ChatOllama
from pydantic import BaseModel

LOCAL_MODEL = "qwen3.6:35b-a3b-q8_0"
CONTEXT_TOKENS = 65_536
MAX_OUTPUT_TOKENS = 16_384
SEED = 20261105


class ToolCallingChatOllama(ChatOllama):
    def with_structured_output(
        self,
        schema: dict[str, Any] | type,
        *,
        method: Literal["function_calling", "json_mode", "json_schema"] = "function_calling",
        include_raw: bool = False,
        **kwargs: Any,
    ) -> Runnable[LanguageModelInput, dict[str, Any] | BaseModel]:
        return super().with_structured_output(schema, method=method, include_raw=include_raw, **kwargs)


def local_model() -> ToolCallingChatOllama:
    return ToolCallingChatOllama(
        model=LOCAL_MODEL,
        name=LOCAL_MODEL,
        reasoning=True,
        seed=SEED,
        num_ctx=CONTEXT_TOKENS,
        num_predict=MAX_OUTPUT_TOKENS,
    )
