from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass

from langchain.mcp import MCPAdapter

from aml_investigator.core.db import open_app_pool
from aml_investigator.evaluation.baselines.runtime import RuntimeSettings
from aml_investigator.evaluation.systems import EvalAlert, SystemDecision
from aml_investigator.investigation.drafters import scripted_drafter
from aml_investigator.investigation.graph import build_graph, postgres_checkpointer
from aml_investigator.investigation.repository import ReviewRepository
from aml_investigator.investigation.service import InvestigationService
from aml_investigator.mcp_server.server import build_server


@dataclass(frozen=True)
class ScriptedInvestigation:
    service: InvestigationService
    name: str = "scripted"

    async def decide(self, alert: EvalAlert) -> SystemDecision:
        outcome = await self.service.investigate(alert.alert_id)
        investigation = await self.service.repository.latest_investigation(alert.alert_id)
        supported = (
            ()
            if investigation is None
            else tuple(
                verdict.typology for verdict in investigation.decision.typologies if verdict.verdict == "supported"
            )
        )
        return SystemDecision(
            recommendation=outcome.recommendation,
            typologies=supported,
            verification_status=outcome.verification_status,
        )


@asynccontextmanager
async def open_scripted(settings: RuntimeSettings) -> AsyncIterator[ScriptedInvestigation]:
    async with open_app_pool(settings.app_conninfo) as pool, MCPAdapter(build_server(settings.mcp_conninfo)) as mcp:
        repository = ReviewRepository(pool)
        graph = build_graph(
            mcp=mcp, drafter=scripted_drafter, repository=repository, checkpointer=await postgres_checkpointer(pool)
        )
        yield ScriptedInvestigation(InvestigationService(graph=graph, repository=repository, mcp=mcp))
