import hashlib
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from importlib import resources
from typing import Any, Literal, cast

from langchain.mcp import MCPAdapter
from langchain_core.language_models import BaseChatModel, LanguageModelInput
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langchain_core.runnables import Runnable
from pydantic import BaseModel

from aml_investigator.evaluation.baselines.runtime import RuntimeSettings
from aml_investigator.evaluation.systems import EvalAlert, SystemDecision
from aml_investigator.investigation.agent.conversation import PROMPTS
from aml_investigator.investigation.agent.evidence import alert_call, evidence_calls
from aml_investigator.investigation.agent.nodes import planned_exchange
from aml_investigator.investigation.agent.state import InvestigatedAlert
from aml_investigator.investigation.agent.tools import AlertTools, ToolCallError
from aml_investigator.investigation.agent.usage import TokenUsage
from aml_investigator.mcp_server.server import build_server
from aml_investigator.monitoring.typologies import Typology

CITATION_RULES = "\n\nCitation rules"
SYSTEM_PROMPT = PROMPTS["system"].partition(CITATION_RULES)[0]
ANSWER_PROMPT = resources.files("aml_investigator.investigation.agent").joinpath("prompts", "b2.md").read_text()
PROMPT_VERSION = hashlib.sha256(f"{SYSTEM_PROMPT}\n{ANSWER_PROMPT}".encode()).hexdigest()[:12]


class SinglePromptAnswer(BaseModel):
    recommendation: Literal["close", "escalate"]
    typologies: list[Typology]
    rationale: str


@dataclass(frozen=True)
class SinglePrompt:
    mcp: MCPAdapter
    model: BaseChatModel
    name: str = "b2"

    async def decide(self, alert: EvalAlert) -> SystemDecision:
        tools = await AlertTools.of(self.mcp, alert.alert_id)
        first = alert_call()
        loaded = await tools.run(first, "evidence")
        if loaded.structured is None:
            raise ToolCallError(f"MCP tool get_alert failed: {loaded.message.text}")
        calls = [first, *evidence_calls(InvestigatedAlert.model_validate(loaded.structured))]
        results = [loaded, *[await tools.run(call, "evidence") for call in calls[1:]]]
        answerer = cast(
            "Runnable[LanguageModelInput, dict[str, Any]]",
            self.model.with_structured_output(SinglePromptAnswer, include_raw=True),
        )
        output = await answerer.ainvoke(
            [SystemMessage(SYSTEM_PROMPT), *planned_exchange(calls, results), HumanMessage(ANSWER_PROMPT)]
        )
        raw: AIMessage = output["raw"]
        usage = TokenUsage.of(raw)
        cost = usage.cost_usd(self.model.get_name())
        trace: dict[str, Any] = {
            "prompt_version": PROMPT_VERSION,
            "tool_calls": [result.trace for result in results],
            "tokens": usage.input_tokens + usage.output_tokens,
            "usage": usage.model_dump(),
            "cost_usd": cost,
        }
        answer = output["parsed"]
        if isinstance(answer, SinglePromptAnswer):
            return SystemDecision(
                recommendation=answer.recommendation,
                typologies=tuple(answer.typologies),
                cost_usd=cost,
                trace={**trace, "parse": "ok", "rationale": answer.rationale},
            )
        reason = output["parsing_error"] or "no answer was returned"
        return SystemDecision(recommendation="escalate", cost_usd=cost, trace={**trace, "parse": f"failed: {reason}"})


@asynccontextmanager
async def open_single_prompt(settings: RuntimeSettings, model: BaseChatModel) -> AsyncIterator[SinglePrompt]:
    async with MCPAdapter(build_server(settings.mcp_conninfo, reference_variant=settings.reference_variant)) as mcp:
        yield SinglePrompt(mcp, model)
