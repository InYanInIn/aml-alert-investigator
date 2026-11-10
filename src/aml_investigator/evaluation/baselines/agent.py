from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass

from langchain.mcp import MCPAdapter
from langchain_core.language_models import BaseChatModel

from aml_investigator.core.db import FixedRole
from aml_investigator.corpus.embeddings import QueryEmbedder, query_embedder
from aml_investigator.evaluation.baselines.runtime import RuntimeSettings
from aml_investigator.evaluation.recordings import CassetteRecorder
from aml_investigator.evaluation.systems import EvalAlert, SystemDecision
from aml_investigator.investigation.agent.conversation import PROFILE_PROMPT, STANDARD_PROMPT
from aml_investigator.investigation.agent.loop import MAX_TOOL_CALLS
from aml_investigator.investigation.service import InvestigationService, open_investigation_service
from aml_investigator.mcp_server.server import build_server


@dataclass(frozen=True)
class AgentInvestigation:
    service: InvestigationService
    name: str
    recorder: CassetteRecorder | None = None

    async def decide(self, alert: EvalAlert) -> SystemDecision:
        outcome = await self.service.investigate(alert.alert_id)
        investigation = await self.service.repository.latest_investigation(alert.alert_id)
        if investigation is None:
            return SystemDecision(
                recommendation=outcome.recommendation, verification_status=outcome.verification_status
            )
        if self.recorder is not None:
            reference = await self.service.repository.investigation_reference(outcome.investigation_id)
            overview = await self.service.repository.alert_overview(alert.alert_id)
            self.recorder.save(reference.thread_id, overview.alert, outcome, investigation.decision)
        return SystemDecision(
            recommendation=outcome.recommendation,
            typologies=tuple(
                verdict.typology for verdict in investigation.decision.typologies if verdict.verdict == "supported"
            ),
            verification_status=outcome.verification_status,
            cost_usd=investigation.trace.get("cost_usd"),
            trace=investigation.trace,
        )


@asynccontextmanager
async def open_agent(
    settings: RuntimeSettings,
    model: BaseChatModel,
    name: str,
    *,
    max_tool_calls: int = MAX_TOOL_CALLS,
    transaction_roles: tuple[FixedRole, FixedRole] | None = None,
    embed_query: QueryEmbedder | None = None,
) -> AsyncIterator[AgentInvestigation]:
    recorder = (
        None
        if settings.recordings is None
        else CassetteRecorder(settings.recordings, settings.database, model.get_name(), settings.reference_variant)
    )
    app_role, mcp_role = transaction_roles or (None, None)
    async with (
        MCPAdapter(
            build_server(
                settings.mcp_conninfo,
                reference_variant=settings.reference_variant,
                embed_query=query_embedder(settings.azure_endpoint) if transaction_roles is None else embed_query,
                customer_profile=settings.customer_profile,
                transaction_role=mcp_role,
            )
        ) as mcp,
        open_investigation_service(
            app_conninfo=settings.app_conninfo,
            mcp=mcp,
            model=model,
            callbacks=[] if recorder is None else [recorder],
            reference_variant=settings.reference_variant,
            max_tool_calls=max_tool_calls,
            prompt=PROFILE_PROMPT if settings.customer_profile else STANDARD_PROMPT,
            transaction_role=app_role,
        ) as service,
    ):
        yield AgentInvestigation(service, name, recorder)
