from datetime import date
from typing import Any, Literal

from psycopg.rows import class_row
from psycopg.types.json import Jsonb

from aml_investigator.core.db import QuerySource
from aml_investigator.core.errors import NotFoundError
from aml_investigator.core.sql import load
from aml_investigator.investigation.decision import AnalystReview, Decision, QaReview
from aml_investigator.investigation.routing import Route
from aml_investigator.investigation.verify import Verification
from aml_investigator.review.records import (
    Alert,
    AlertOverview,
    AlertSummary,
    AutoClosedAlert,
    InvestigationReference,
    InvestigationView,
    QueueItem,
    RecordedQaReview,
    RecordedReview,
    RuleHit,
)

ALERTS_SQL = load(__package__, "alerts")
ANALYST_QUEUE_SQL = load(__package__, "analyst_queue")
AUTO_CLOSED_SQL = load(__package__, "auto_closed")
UNINVESTIGATED_ALERTS_SQL = load(__package__, "uninvestigated_alerts")
LATEST_INVESTIGATION_SQL = load(__package__, "latest_investigation")
SAVE_INVESTIGATION_SQL = load(__package__, "save_investigation")
SIMULATION_DECISION_SQL = load(__package__, "simulation_decision")
PERIODS_SQL = load(__package__, "periods")

ALERT_SQL = load(__package__, "alert")
RULE_HITS_SQL = load(__package__, "alert_rule_hits")
INVESTIGATION_REFERENCE_SQL = load(__package__, "investigation_reference")
SAVE_DECISION_SQL = load(__package__, "save_decision")
SAVE_QA_REVIEW_SQL = load(__package__, "save_qa_review")


class AlertNotFoundError(NotFoundError):
    def __init__(self, alert_id: int) -> None:
        super().__init__("alert", alert_id)


class InvestigationNotFoundError(NotFoundError):
    def __init__(self, investigation_id: int) -> None:
        super().__init__("investigation", investigation_id)


class ReviewRepository:
    def __init__(self, pool: QuerySource) -> None:
        self.pool = pool

    async def alerts(self, period: int | None = None) -> list[AlertSummary]:
        async with self.pool.connection() as conn, conn.cursor(row_factory=class_row(AlertSummary)) as cursor:
            await cursor.execute(ALERTS_SQL, {"period": period})
            return await cursor.fetchall()

    async def analyst_queue(self, period: int | None = None) -> list[QueueItem]:
        async with self.pool.connection() as conn, conn.cursor(row_factory=class_row(QueueItem)) as cursor:
            await cursor.execute(ANALYST_QUEUE_SQL, {"period": period})
            return await cursor.fetchall()

    async def periods(self) -> list[dict[str, Any]]:
        async with self.pool.connection() as conn:
            return await (await conn.execute(PERIODS_SQL)).fetchall()

    async def simulation_decision(self, alert_id: int) -> dict[str, Any] | None:
        async with self.pool.connection() as conn:
            return await (await conn.execute(SIMULATION_DECISION_SQL, (alert_id,))).fetchone()

    async def auto_closed(self) -> list[AutoClosedAlert]:
        async with self.pool.connection() as conn, conn.cursor(row_factory=class_row(AutoClosedAlert)) as cursor:
            await cursor.execute(AUTO_CLOSED_SQL)
            return await cursor.fetchall()

    async def uninvestigated_alerts(self, day: date, limit: int | None) -> list[int]:
        async with self.pool.connection() as conn:
            cursor = await conn.execute(UNINVESTIGATED_ALERTS_SQL, {"day": day, "limit": limit})
            return [int(row["alert_id"]) for row in await cursor.fetchall()]

    async def alert_overview(self, alert_id: int) -> AlertOverview:
        async with self.pool.connection() as conn:
            async with conn.cursor(row_factory=class_row(Alert)) as cursor:
                await cursor.execute(ALERT_SQL, (alert_id,))
                alert = await cursor.fetchone()
            if alert is None:
                raise AlertNotFoundError(alert_id)
            async with conn.cursor(row_factory=class_row(RuleHit)) as cursor:
                await cursor.execute(RULE_HITS_SQL, (alert_id,))
                return AlertOverview(alert=alert, rule_hits=await cursor.fetchall())

    async def latest_investigation(self, alert_id: int) -> InvestigationView | None:
        async with self.pool.connection() as conn:
            row = await (await conn.execute(LATEST_INVESTIGATION_SQL, (alert_id,))).fetchone()
        return None if row is None else investigation_view(row)

    async def investigation_reference(self, investigation_id: int) -> InvestigationReference:
        async with self.pool.connection() as conn, conn.cursor(row_factory=class_row(InvestigationReference)) as cursor:
            await cursor.execute(INVESTIGATION_REFERENCE_SQL, (investigation_id,))
            reference = await cursor.fetchone()
        if reference is None:
            raise InvestigationNotFoundError(investigation_id)
        return reference

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
