from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Literal

from aml_investigator.investigation.decision import Decision
from aml_investigator.investigation.routing import Route
from aml_investigator.investigation.verify import Verification


@dataclass(frozen=True)
class AlertSummary:
    alert_id: int
    account_id: str
    alert_date: date
    rule_hits: str
    status: str


@dataclass(frozen=True)
class QueueItem:
    kind: Literal["escalation", "qa_sample"]
    investigation_id: int
    alert_id: int
    account_id: str
    alert_date: date
    rule_hits: str
    recommendation: Literal["close", "escalate"]
    verification_status: Literal["passed", "failed"]
    created_at: datetime


@dataclass(frozen=True)
class AutoClosedAlert:
    investigation_id: int
    alert_id: int
    account_id: str
    alert_date: date
    route: Literal["auto_closed", "qa_sample"]
    qa_status: Literal["not_sampled", "pending", "confirmed", "reopened"]
    closed_at: datetime


@dataclass(frozen=True)
class Alert:
    alert_id: int
    account_id: str
    alert_date: date
    window_start: datetime
    window_end: datetime


@dataclass(frozen=True)
class RuleHit:
    rule_id: str
    score: Decimal
    evidence_txn_ids: list[int]


@dataclass(frozen=True)
class AlertOverview:
    alert: Alert
    rule_hits: list[RuleHit]


@dataclass(frozen=True)
class RecordedReview:
    analyst: str
    final_decision: Literal["close", "escalate"]
    comment: str
    decided_at: datetime


@dataclass(frozen=True)
class RecordedQaReview:
    analyst: str
    outcome: Literal["confirmed", "reopened"]
    comment: str
    reviewed_at: datetime


@dataclass(frozen=True)
class InvestigationView:
    investigation_id: int
    recommendation: Literal["close", "escalate"]
    route: Route
    decision: Decision
    verification: Verification
    trace: dict[str, Any]
    created_at: datetime
    review: RecordedReview | None
    qa_review: RecordedQaReview | None


@dataclass(frozen=True)
class InvestigationReference:
    alert_id: int
    thread_id: str
    route: Route
    decided: bool
    qa_reviewed: bool
