from collections import defaultdict, deque
from pathlib import Path
from typing import Any, Literal, cast
from uuid import UUID

from fastmcp import FastMCP
from langchain.mcp import MCPAdapter
from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.callbacks.manager import CallbackManagerForLLMRun
from langchain_core.language_models import BaseChatModel, LanguageModelInput
from langchain_core.messages import AIMessage, BaseMessage, ToolCall, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult, LLMResult
from langchain_core.runnables import Runnable, RunnableConfig
from langchain_core.tools import BaseTool
from langchain_core.utils.function_calling import convert_to_openai_tool
from mcp.client.caching import CacheMode
from pydantic import BaseModel

from aml_investigator.evaluation.results import GitState
from aml_investigator.investigation.agent.conversation import PROMPT_VERSION
from aml_investigator.investigation.decision import Decision
from aml_investigator.investigation.routing import Route
from aml_investigator.investigation.service import InvestigationOutcome, open_investigation_service
from aml_investigator.mcp_server.schemas import ReferenceVariant
from aml_investigator.mcp_server.server import build_server
from aml_investigator.review.records import Alert


class ReplayMismatchError(AssertionError):
    pass


class RecordedOutcome(BaseModel):
    recommendation: Literal["close", "escalate"]
    verification_status: Literal["passed", "failed"]
    route: Route
    decision: Decision


class RecordedToolCall(BaseModel):
    tool: str
    args: dict[str, Any]
    result: ToolMessage


class Cassette(BaseModel):
    model: str
    prompt_version: str
    git_commit: str
    reference_variant: ReferenceVariant
    alert: Alert
    outcome: RecordedOutcome
    model_responses: list[AIMessage]
    tool_calls: list[RecordedToolCall]

    @classmethod
    def read(cls, path: Path) -> "Cassette":
        return cls.model_validate_json(path.read_text())


class CassetteRecorder(BaseCallbackHandler):
    run_inline = True

    def __init__(self, root: Path, database: str, model: str, reference_variant: ReferenceVariant) -> None:
        self.directory = root / database / model.replace(":", "-")
        self.model = model
        self.reference_variant = reference_variant
        self.git_commit = GitState.capture().commit
        self.model_threads: dict[UUID, str] = {}
        self.tool_starts: dict[UUID, tuple[str, str, dict[str, Any]]] = {}
        self.model_responses: defaultdict[str, list[AIMessage]] = defaultdict(list)
        self.tool_calls: defaultdict[str, list[RecordedToolCall]] = defaultdict(list)

    def on_chat_model_start(
        self,
        serialized: dict[str, Any],  # noqa: ARG002
        messages: list[list[BaseMessage]],  # noqa: ARG002
        *,
        run_id: UUID,
        metadata: dict[str, Any] | None = None,
        **kwargs: Any,  # noqa: ARG002
    ) -> None:
        self.model_threads[run_id] = thread_of(metadata)

    def on_llm_end(self, response: LLMResult, *, run_id: UUID, **kwargs: Any) -> None:  # noqa: ARG002
        generation = cast("ChatGeneration", response.generations[0][0])
        self.model_responses[self.model_threads.pop(run_id)].append(cast("AIMessage", generation.message))

    def on_tool_start(
        self,
        serialized: dict[str, Any],
        input_str: str,  # noqa: ARG002
        *,
        run_id: UUID,
        metadata: dict[str, Any] | None = None,
        inputs: dict[str, Any] | None = None,
        **kwargs: Any,  # noqa: ARG002
    ) -> None:
        self.tool_starts[run_id] = (thread_of(metadata), serialized["name"], inputs or {})

    def on_tool_end(self, output: Any, *, run_id: UUID, **kwargs: Any) -> None:  # noqa: ARG002
        thread_id, tool, args = self.tool_starts.pop(run_id)
        self.tool_calls[thread_id].append(RecordedToolCall(tool=tool, args=args, result=output))

    def save(self, thread_id: str, alert: Alert, outcome: InvestigationOutcome, decision: Decision) -> Path:
        cassette = Cassette(
            model=self.model,
            prompt_version=PROMPT_VERSION,
            git_commit=self.git_commit,
            reference_variant=self.reference_variant,
            alert=alert,
            outcome=RecordedOutcome(
                recommendation=outcome.recommendation,
                verification_status=outcome.verification_status,
                route=outcome.route,
                decision=decision,
            ),
            model_responses=self.model_responses.pop(thread_id, []),
            tool_calls=self.tool_calls.pop(thread_id, []),
        )
        path = self.directory / f"{alert.alert_id}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(cassette.model_dump_json(indent=2) + "\n")
        return path


def thread_of(metadata: dict[str, Any] | None) -> str:
    return str((metadata or {})["thread_id"])


async def replay(cassette: Cassette, *, app_conninfo: str, mcp_conninfo: str) -> InvestigationOutcome:
    model = ReplayChatModel(name=cassette.model, responses=list(cassette.model_responses))
    server = build_server(mcp_conninfo, reference_variant=cassette.reference_variant)
    async with (
        ReplayMcp(server, cassette.tool_calls) as mcp,
        open_investigation_service(
            app_conninfo=app_conninfo, mcp=mcp, model=model, reference_variant=cassette.reference_variant
        ) as service,
    ):
        outcome = await service.investigate(cassette.alert.alert_id)
    if model.responses or mcp.calls:
        raise ReplayMismatchError(
            f"the investigation left {len(model.responses)} recorded model responses "
            f"and {len(mcp.calls)} recorded tool calls unused"
        )
    return outcome


class ReplayChatModel(BaseChatModel):
    responses: list[AIMessage]

    @property
    def _llm_type(self) -> str:
        return "replay"

    def bind_tools(
        self, tools: Any, *, tool_choice: str | None = None, **kwargs: Any
    ) -> Runnable[LanguageModelInput, AIMessage]:
        return self.bind(tools=[convert_to_openai_tool(tool) for tool in tools], tool_choice=tool_choice, **kwargs)

    def _generate(
        self,
        messages: list[BaseMessage],  # noqa: ARG002
        stop: list[str] | None = None,  # noqa: ARG002
        run_manager: CallbackManagerForLLMRun | None = None,  # noqa: ARG002
        **kwargs: Any,  # noqa: ARG002
    ) -> ChatResult:
        if not self.responses:
            raise ReplayMismatchError("the model was called once more than recorded")
        return ChatResult(generations=[ChatGeneration(message=self.responses.pop(0))])


class ReplayMcp(MCPAdapter):
    def __init__(self, server: FastMCP[Any], calls: list[RecordedToolCall]) -> None:
        super().__init__(server)
        self.calls = deque(calls)

    async def list_tools(self, *, cache_mode: CacheMode = "use") -> list[BaseTool]:
        return [
            ReplayedTool(name=tool.name, description=tool.description, args_schema=tool.args_schema, source=self)
            for tool in await super().list_tools(cache_mode=cache_mode)
        ]

    def answer(self, call: ToolCall) -> ToolMessage:
        called = f"{call['name']}({call['args']})"
        if not self.calls:
            raise ReplayMismatchError(f"unrecorded tool call {called}: no recorded tool call is left")
        recorded = self.calls.popleft()
        if (recorded.tool, recorded.args) != (call["name"], call["args"]):
            raise ReplayMismatchError(f"tool call {called} differs from the recorded {recorded.tool}({recorded.args})")
        return recorded.result.model_copy(update={"tool_call_id": call["id"]})


class ReplayedTool(BaseTool):
    source: ReplayMcp

    def _run(self, *args: Any, **kwargs: Any) -> Any:  # noqa: ARG002
        raise ReplayMismatchError(f"replayed tool {self.name} answers only tool calls through ainvoke")

    async def ainvoke(
        self,
        input: str | dict[str, Any] | ToolCall,
        config: RunnableConfig | None = None,  # noqa: ARG002
        **kwargs: Any,  # noqa: ARG002
    ) -> Any:
        return self.source.answer(cast("ToolCall", input))
