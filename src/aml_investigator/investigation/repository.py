from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Literal

from psycopg.rows import class_row
from psycopg.types.json import Jsonb

from aml_investigator.core.db import Pool
from aml_investigator.core.sql import load
from aml_investigator.investigation.decision import AnalystReview, Decision
from aml_investigator.investigation.verify import Verification

QUEUE_SQL = load(__package__, "alert_queue")
LATEST_INVESTIGATION_SQL = load(__package__, "latest_investigation")
SAVE_INVESTIGATION_SQL = load(__package__, "save_investigation")

ALERT_SQL = "SELECT alert_id, account_id, alert_date, window_start, window_end FROM core.alerts WHERE alert_id = %s"

RULE_HITS_SQL = "SELECT rule_id, score, evidence_txn_ids FROM core.alert_rule_hits WHERE alert_id = %s ORDER BY rule_id"

INVESTIGATION_REFERENCE_SQL = """
SELECT i.alert_id, i.thread_id, d.decision_id IS NOT NULL AS decided
FROM review.investigations i
LEFT JOIN review.decisions d ON d.investigation_id = i.investigation_id
WHERE i.investigation_id = %s
"""

SAVE_DECISION_SQL = (
    "INSERT INTO review.decisions (investigation_id, analyst, final_decision, comment) "
    "VALUES (%s, %s, %s, %s) ON CONFLICT (investigation_id) DO NOTHING"
)


@dataclass(frozen=True)
class QueuedAlert:
    alert_id: int
    account_id: str
    alert_date: date
    rule_hits: str
    status: str


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
class InvestigationView:
    investigation_id: int
    recommendation: Literal["close", "escalate"]
    decision: Decision
    verification: Verification
    created_at: datetime
    review: RecordedReview | None


@dataclass(frozen=True)
class InvestigationReference:
    alert_id: int
    thread_id: str
    decided: bool


class ReviewRepository:
    def __init__(self, pool: Pool) -> None:
        self.pool = pool

    async def alert_queue(self) -> list[QueuedAlert]:
        async with self.pool.connection() as conn, conn.cursor(row_factory=class_row(QueuedAlert)) as cursor:
            await cursor.execute(QUEUE_SQL)
            return await cursor.fetchall()

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
                },
            )
            [row] = await cursor.fetchall()
        return int(row["investigation_id"])

    async def save_decision(self, investigation_id: int, review: AnalystReview) -> None:
        async with self.pool.connection() as conn:
            await conn.execute(
                SAVE_DECISION_SQL, (investigation_id, review.analyst, review.final_decision, review.comment)
            )


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
    return InvestigationView(
        investigation_id=row["investigation_id"],
        recommendation=row["recommendation"],
        decision=Decision.model_validate(row["decision"]),
        verification=Verification.model_validate(row["verification"]),
        created_at=row["created_at"],
        review=review,
    )
