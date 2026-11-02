from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass

from langchain.mcp import MCPAdapter

from aml_investigator.evaluation.baselines.runtime import RuntimeSettings
from aml_investigator.evaluation.systems import EvalAlert, SystemDecision
from aml_investigator.investigation.rule_score_model import rule_score_model
from aml_investigator.investigation.service import InvestigationService, open_investigation_service
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
    async with (
        MCPAdapter(build_server(settings.mcp_conninfo)) as mcp,
        open_investigation_service(
            app_conninfo=settings.app_conninfo, mcp=mcp, model=rule_score_model(settings.database)
        ) as service,
    ):
        yield ScriptedInvestigation(service)
