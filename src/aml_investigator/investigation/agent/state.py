from datetime import date, datetime
from typing import Any, Literal, TypedDict

from langchain_core.messages import BaseMessage
from pydantic import BaseModel

from aml_investigator.investigation.decision import AnalystReview, Decision
from aml_investigator.investigation.routing import Route
from aml_investigator.investigation.verify import Provenance, Verification

LoopStop = Literal["skipped", "model_done", "tool_call_limit", "token_budget"]


class AlertRuleHit(BaseModel):
    rule_id: str
    evidence_txn_ids: list[int]


class InvestigatedAlert(BaseModel):
    account_id: str
    alert_date: date
    as_of: datetime
    rule_hits: list[AlertRuleHit]


class InvestigationState(TypedDict, total=False):
    alert_id: int
    alert: InvestigatedAlert
    messages: list[BaseMessage]
    provenance: Provenance
    tool_calls: list[dict[str, Any]]
    loop_stop: LoopStop
    tokens: int
    draft: Decision | None
    draft_output: str
    draft_errors: list[str]
    repairs: int
    verification: Verification
    decision: Decision
    recommendation: Literal["close", "escalate"]
    route: Route
    investigation_id: int
    review: AnalystReview


CHECKPOINTED_TYPES = (InvestigatedAlert, Provenance, Decision, Verification, AnalystReview)
