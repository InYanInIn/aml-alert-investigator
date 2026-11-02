from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Literal

from psycopg.rows import class_row
from psycopg.types.json import Jsonb

from aml_investigator.core.db import Pool
from aml_investigator.core.sql import load
from aml_investigator.investigation.decision import AnalystReview, Decision, QaReview
from aml_investigator.investigation.routing import Route
from aml_investigator.investigation.verify import Verification

ALERTS_SQL = load(__package__, "alerts")
ANALYST_QUEUE_SQL = load(__package__, "analyst_queue")
AUTO_CLOSED_SQL = load(__package__, "auto_closed")
UNINVESTIGATED_ALERTS_SQL = load(__package__, "uninvestigated_alerts")
LATEST_INVESTIGATION_SQL = load(__package__, "latest_investigation")
SAVE_INVESTIGATION_SQL = load(__package__, "save_investigation")

ALERT_SQL = load(__package__, "alert")
RULE_HITS_SQL = load(__package__, "alert_rule_hits")
INVESTIGATION_REFERENCE_SQL = load(__package__, "investigation_reference")
SAVE_DECISION_SQL = load(__package__, "save_decision")
SAVE_QA_REVIEW_SQL = load(__package__, "save_qa_review")


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


class ReviewRepository:
    def __init__(self, pool: Pool) -> None:
        self.pool = pool

    async def alerts(self) -> list[AlertSummary]:
        async with self.pool.connection() as conn, conn.cursor(row_factory=class_row(AlertSummary)) as cursor:
            await cursor.execute(ALERTS_SQL)
            return await cursor.fetchall()

    async def analyst_queue(self) -> list[QueueItem]:
        async with self.pool.connection() as conn, conn.cursor(row_factory=class_row(QueueItem)) as cursor:
            await cursor.execute(ANALYST_QUEUE_SQL)
            return await cursor.fetchall()

    async def auto_closed(self) -> list[AutoClosedAlert]:
        async with self.pool.connection() as conn, conn.cursor(row_factory=class_row(AutoClosedAlert)) as cursor:
            await cursor.execute(AUTO_CLOSED_SQL)
            return await cursor.fetchall()

    async def uninvestigated_alerts(self, day: date, limit: int | None) -> list[int]:
        async with self.pool.connection() as conn:
            cursor = await conn.execute(UNINVESTIGATED_ALERTS_SQL, {"day": day, "limit": limit})
            return [int(row["alert_id"]) for row in await cursor.fetchall()]

    async def alert_overview(self, alert_id: int) -> AlertOverview | None:
        async with self.pool.connection() as conn:
            async with conn.cursor(row_factory=class_row(Alert)) as cursor:
                await cursor.execute(ALERT_SQL, (alert_id,))
                alert = await cursor.fetchone()
            if alert is None:
                return None
            async with conn.cursor(row_factory=class_row(RuleHit)) as cursor:
                await cursor.execute(RULE_HITS_SQL, (alert_id,))
                return AlertOverview(alert=alert, rule_hits=await cursor.fetchall())

    async def latest_investigation(self, alert_id: int) -> InvestigationView | None:
        async with self.pool.connection() as conn:
            row = await (await conn.execute(LATEST_INVESTIGATION_SQL, (alert_id,))).fetchone()
        return None if row is None else investigation_view(row)

    async def investigation_reference(self, investigation_id: int) -> InvestigationReference | None:
        async with self.pool.connection() as conn, conn.cursor(row_factory=class_row(InvestigationReference)) as cursor:
            await cursor.execute(INVESTIGATION_REFERENCE_SQL, (investigation_id,))
            return await cursor.fetchone()

    async def save_investigation(
        self,
        *,
        alert_id: int,
        thread_id: str,
        model: str,
        prompt_version: str,
        recommendation: Literal["close", "escalate"],
        decision: Decision,
        verification: Verification,
        route: Route,
        trace: dict[str, Any],
    ) -> int:
        async with self.pool.connection() as conn:
            cursor = await conn.execute(
                SAVE_INVESTIGATION_SQL,
                {
                    "alert_id": alert_id,
                    "thread_id": thread_id,
                    "model": model,
                    "prompt_version": prompt_version,
                    "recommendation": recommendation,
                    "decision": Jsonb(decision.model_dump()),
                    "verification": Jsonb(verification.model_dump()),
                    "route": route,
                    "trace": Jsonb(trace),
                },
            )
            [row] = await cursor.fetchall()
        return int(row["investigation_id"])

    async def save_decision(self, investigation_id: int, review: AnalystReview) -> None:
        async with self.pool.connection() as conn:
            await conn.execute(
                SAVE_DECISION_SQL, (investigation_id, review.analyst, review.final_decision, review.comment)
            )

    async def save_qa_review(self, investigation_id: int, review: QaReview) -> None:
        async with self.pool.connection() as conn:
            await conn.execute(SAVE_QA_REVIEW_SQL, (investigation_id, review.analyst, review.outcome, review.comment))


def investigation_view(row: dict[str, Any]) -> InvestigationView:
    review = (
        None
        if row["final_decision"] is None
        else RecordedReview(
            analyst=row["analyst"],
            final_decision=row["final_decision"],
            comment=row["comment"],
            decided_at=row["decided_at"],
        )
    )
    qa_review = (
        None
        if row["qa_outcome"] is None
        else RecordedQaReview(
            analyst=row["qa_analyst"],
            outcome=row["qa_outcome"],
            comment=row["qa_comment"],
            reviewed_at=row["qa_reviewed_at"],
        )
    )
    return InvestigationView(
        investigation_id=row["investigation_id"],
        recommendation=row["recommendation"],
        route=row["route"],
        decision=Decision.model_validate(row["decision"]),
        verification=Verification.model_validate(row["verification"]),
        trace=row["trace"],
        created_at=row["created_at"],
        review=review,
        qa_review=qa_review,
    )
