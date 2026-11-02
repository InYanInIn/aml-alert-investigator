import uuid
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any

from langchain_core.callbacks import CallbackManagerForLLMRun
from langchain_core.language_models import BaseChatModel, LanguageModelInput
from langchain_core.messages import AIMessage, BaseMessage
from langchain_core.messages.ai import UsageMetadata
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.runnables import Runnable
from langchain_core.tools import BaseTool
from langchain_core.utils.function_calling import convert_to_openai_tool
from pydantic import Field

from aml_investigator.investigation.decision import Decision


@dataclass(frozen=True)
class ModelCall:
    messages: list[BaseMessage]
    tools: list[dict[str, Any]]

    @property
    def tool_names(self) -> list[str]:
        return [tool["function"]["name"] for tool in self.tools]

    @property
    def drafting(self) -> bool:
        return self.tool_names == [Decision.__name__]


class ScriptedChatModel(BaseChatModel):
    """Replays a fixed sequence of replies and records what it was asked."""

    replies: list[AIMessage]
    calls: list[ModelCall] = Field(default_factory=list)
    name: str | None = "scripted"

    @property
    def _llm_type(self) -> str:
        return "scripted"

    def bind_tools(
        self,
        tools: Sequence[dict[str, Any] | type | Callable[..., Any] | BaseTool],
        *,
        tool_choice: str | None = None,
        **kwargs: Any,
    ) -> Runnable[LanguageModelInput, AIMessage]:
        return self.bind(tools=[convert_to_openai_tool(tool) for tool in tools], tool_choice=tool_choice, **kwargs)

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,  # noqa: ARG002
        run_manager: CallbackManagerForLLMRun | None = None,  # noqa: ARG002
        **kwargs: Any,
    ) -> ChatResult:
        self.calls.append(ModelCall(list(messages), list(kwargs.get("tools", []))))
        if not self.replies:
            raise AssertionError(f"scripted model has no reply left for call {len(self.calls)}")
        return ChatResult(generations=[ChatGeneration(message=self.replies.pop(0))])


def usage(tokens: int) -> UsageMetadata:
    return {"input_tokens": tokens, "output_tokens": 0, "total_tokens": tokens}


def text_reply(text: str, tokens: int = 0) -> AIMessage:
    return AIMessage(text, usage_metadata=usage(tokens))


def tool_reply(*calls: tuple[str, dict[str, Any]], tokens: int = 0) -> AIMessage:
    return AIMessage(
        "",
        tool_calls=[{"name": name, "args": args, "id": f"call-{uuid.uuid4().hex[:8]}"} for name, args in calls],
        usage_metadata=usage(tokens),
    )


def decision_reply(decision: Decision, tokens: int = 0) -> AIMessage:
    return AIMessage(
        "",
        tool_calls=[{"name": Decision.__name__, "args": decision.model_dump(), "id": "call-decision"}],
        usage_metadata=usage(tokens),
    )
