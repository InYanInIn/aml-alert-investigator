from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass

from langchain.mcp import MCPAdapter
from langchain_core.language_models import BaseChatModel

from aml_investigator.evaluation.baselines.runtime import RuntimeSettings
from aml_investigator.evaluation.systems import EvalAlert, SystemDecision
from aml_investigator.investigation.service import InvestigationService, open_investigation_service
from aml_investigator.mcp_server.server import build_server


@dataclass(frozen=True)
class AgentInvestigation:
    service: InvestigationService
    name: str

    async def decide(self, alert: EvalAlert) -> SystemDecision:
        outcome = await self.service.investigate(alert.alert_id)
        investigation = await self.service.repository.latest_investigation(alert.alert_id)
        if investigation is None:
            return SystemDecision(
                recommendation=outcome.recommendation, verification_status=outcome.verification_status
            )
        return SystemDecision(
            recommendation=outcome.recommendation,
            typologies=tuple(
                verdict.typology for verdict in investigation.decision.typologies if verdict.verdict == "supported"
            ),
            verification_status=outcome.verification_status,
            trace=investigation.trace,
        )


@asynccontextmanager
async def open_agent(settings: RuntimeSettings, model: BaseChatModel, name: str) -> AsyncIterator[AgentInvestigation]:
    async with (
        MCPAdapter(build_server(settings.mcp_conninfo, reference_variant=settings.reference_variant)) as mcp,
        open_investigation_service(
            app_conninfo=settings.app_conninfo,
            mcp=mcp,
            model=model,
            reference_variant=settings.reference_variant,
        ) as service,
    ):
        yield AgentInvestigation(service, name)
