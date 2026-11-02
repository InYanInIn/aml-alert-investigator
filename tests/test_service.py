from collections.abc import AsyncIterator
from datetime import date, datetime, timedelta
from typing import Any

import pytest
from langchain.mcp import MCPAdapter

from aml_investigator.core.db import open_app_pool
from aml_investigator.investigation.decision import AnalystReview, QaReview
from aml_investigator.investigation.models.rule_score import RuleScoreChatModel
from aml_investigator.investigation.routing import qa_sampled
from aml_investigator.investigation.service import (
    InvestigationService,
    QaSampleNotFoundError,
    open_investigation_service,
)
from aml_investigator.mcp_server.server import build_server
from aml_investigator.review.pages import ReviewPages
from aml_investigator.review.repository import AlertNotFoundError, InvestigationNotFoundError, ReviewRepository
from tests.conftest import Database

REVIEW = AnalystReview(analyst="jan", final_decision="close", comment="known payroll pattern")
QA = QaReview(analyst="eve", outcome="confirmed", comment="looks right")
ESCALATING = RuleScoreChatModel(thresholds={"R2_fan_out": 1.0}, close_below=1.0)
CLOSING = RuleScoreChatModel(thresholds={"R2_fan_out": 1.0}, close_below=3.0)


pytestmark = pytest.mark.usefixtures("regulations")


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def add_alert(db: Database, alert_date: date, account: str = "A1") -> int:
    with db.owner() as conn:
        row = conn.execute(
            "INSERT INTO core.alerts (account_id, alert_date, window_start, window_end) "
            "VALUES (%s, %s, '2022-09-01', %s) RETURNING alert_id",
            (account, alert_date, alert_date + timedelta(days=1)),
        ).fetchone()
        assert row is not None
        created = int(str(row[0]))
        conn.execute("INSERT INTO core.alert_rule_hits VALUES (%s, 'R2_fan_out', 2.0, ARRAY[11, 12])", (created,))
    return created


@pytest.fixture
def alert_id(db: Database) -> int:
    with db.owner() as conn:
        for account in ("A1", "A2", "A3"):
            conn.execute("INSERT INTO core.accounts VALUES (%s, 1, 'Bank', %s, 'Corporation')", (account, account))
        for txn_id, ts, receiver, amount in (
            (11, datetime(2022, 9, 2, 10, 15), "A2", 2848.96),
            (12, datetime(2022, 9, 2, 11, 0), "A3", 1500.0),
        ):
            conn.execute(
                "INSERT INTO core.transactions VALUES (%s, %s, 'A1', %s, %s, 'Euro', %s, 'Euro', 'ACH', %s)",
                (txn_id, ts, receiver, amount, amount, amount),
            )
    return add_alert(db, date(2022, 9, 2))


def first_day(sampled: bool) -> date:
    day = date(2022, 9, 3)
    while qa_sampled("A1", day) != sampled:
        day += timedelta(days=1)
    return day


@pytest.fixture
async def mcp(db: Database) -> AsyncIterator[MCPAdapter]:
    async with MCPAdapter(build_server(db.mcp_dsn)) as adapter:
        yield adapter


@pytest.fixture
async def service(db: Database, mcp: MCPAdapter) -> AsyncIterator[InvestigationService]:
    async with open_investigation_service(app_conninfo=db.app_dsn, mcp=mcp, model=ESCALATING) as service:
        yield service


@pytest.fixture
async def closing(db: Database, mcp: MCPAdapter) -> AsyncIterator[InvestigationService]:
    async with open_investigation_service(app_conninfo=db.app_dsn, mcp=mcp, model=CLOSING) as service:
        yield service


@pytest.fixture
async def pages(db: Database, mcp: MCPAdapter) -> AsyncIterator[ReviewPages]:
    async with open_app_pool(db.app_dsn) as pool:
        yield ReviewPages(ReviewRepository(pool), mcp)


def rows(db: Database, query: str) -> list[tuple[Any, ...]]:
    with db.owner() as conn:
        return conn.execute(query).fetchall()


@pytest.mark.anyio
async def test_investigate_then_decide_without_http(
    db: Database, alert_id: int, service: InvestigationService, pages: ReviewPages
) -> None:
    outcome = await service.investigate(alert_id)

    [(investigation_id, stored_alert_id, thread_id, recommendation, route)] = rows(
        db, "SELECT investigation_id, alert_id, thread_id, recommendation, route FROM review.investigations"
    )
    assert outcome.investigation_id == investigation_id
    assert (outcome.recommendation, outcome.verification_status, outcome.route) == ("escalate", "passed", "escalated")
    assert (stored_alert_id, recommendation, route) == (alert_id, "escalate", "escalated")
    assert thread_id.startswith(f"{alert_id}:")
    [queued] = await pages.analyst_queue()
    assert (queued.kind, queued.investigation_id, queued.verification_status) == (
        "escalation",
        investigation_id,
        "passed",
    )

    assert await service.record_decision(investigation_id, REVIEW) == alert_id

    assert rows(db, "SELECT investigation_id, analyst, final_decision, comment FROM review.decisions") == [
        (investigation_id, "jan", "close", "known payroll pattern")
    ]
    assert await pages.analyst_queue() == []
    assert [alert.status for alert in await pages.alerts()] == ["decided: close"]
    page = await pages.alert_page(alert_id)
    assert page.investigation is not None
    assert page.investigation.review is not None
    assert page.investigation.review.final_decision == "close"
    assert page.investigation.trace["loop_stop"] == "model_done"
    assert [txn["txn_id"] for txn in page.evidence["transactions"]] == [11, 12]
    assert [txn["txn_id"] for txn in page.cited_transactions] == [11, 12]
    assert {passage["passage_id"] for passage in page.cited_passages} == {
        "AMLR art. 69(1)",
        "PL-AML art. 74 ust. 1",
        "AMLR art. 34(2)",
    }
    assert all(len(passage["content_sha256"]) == 64 for passage in page.cited_passages)


@pytest.mark.anyio
async def test_second_decision_is_ignored(db: Database, alert_id: int, service: InvestigationService) -> None:
    outcome = await service.investigate(alert_id)
    await service.record_decision(outcome.investigation_id, REVIEW)

    override = AnalystReview(analyst="eve", final_decision="escalate", comment="second opinion")
    assert await service.record_decision(outcome.investigation_id, override) == alert_id

    assert rows(db, "SELECT analyst, final_decision FROM review.decisions") == [("jan", "close")]


@pytest.mark.anyio
async def test_each_investigation_gets_a_fresh_thread(
    db: Database, alert_id: int, service: InvestigationService, pages: ReviewPages
) -> None:
    first = await service.investigate(alert_id)
    second = await service.investigate(alert_id)

    assert first.investigation_id != second.investigation_id
    [(threads,)] = rows(db, "SELECT count(DISTINCT thread_id) FROM review.investigations")
    assert threads == 2
    [queued] = await pages.analyst_queue()
    assert queued.investigation_id == second.investigation_id


@pytest.mark.anyio
async def test_auto_closed_alerts_and_qa_review(
    db: Database, alert_id: int, closing: InvestigationService, pages: ReviewPages
) -> None:
    unsampled = add_alert(db, first_day(sampled=False))
    sampled = add_alert(db, first_day(sampled=True))
    plain = await closing.investigate(unsampled)
    qa = await closing.investigate(sampled)

    assert (plain.route, qa.route) == ("auto_closed", "qa_sample")
    assert rows(db, "SELECT analyst, final_decision FROM review.decisions") == [("auto-close", "close")] * 2
    [queued] = await pages.analyst_queue()
    assert (queued.kind, queued.investigation_id, queued.alert_id) == ("qa_sample", qa.investigation_id, sampled)
    closed = {item.alert_id: (item.route, item.qa_status) for item in await pages.auto_closed()}
    assert closed == {unsampled: ("auto_closed", "not_sampled"), sampled: ("qa_sample", "pending")}
    statuses = {alert.alert_id: alert.status for alert in await pages.alerts()}
    assert statuses == {alert_id: "none", unsampled: "auto-closed", sampled: "auto-closed, QA pending"}

    assert await closing.record_qa_review(qa.investigation_id, QA) == sampled
    reopen = QaReview(analyst="bob", outcome="reopened")
    assert await closing.record_qa_review(qa.investigation_id, reopen) == sampled

    assert rows(db, "SELECT investigation_id, analyst, outcome, comment FROM review.qa_reviews") == [
        (qa.investigation_id, "eve", "confirmed", "looks right")
    ]
    assert await pages.analyst_queue() == []
    assert {item.alert_id: item.qa_status for item in await pages.auto_closed()}[sampled] == "confirmed"
    assert {alert.alert_id: alert.status for alert in await pages.alerts()}[sampled] == "auto-closed, QA confirmed"
    page = await pages.alert_page(sampled)
    assert page.investigation is not None
    assert page.investigation.route == "qa_sample"
    assert page.investigation.qa_review is not None
    assert (page.investigation.qa_review.analyst, page.investigation.qa_review.outcome) == ("eve", "confirmed")

    assert await closing.record_decision(plain.investigation_id, REVIEW) == unsampled
    assert rows(db, "SELECT count(*) FROM review.decisions") == [(2,)]


@pytest.mark.anyio
async def test_qa_review_needs_a_qa_sample(db: Database, alert_id: int, service: InvestigationService) -> None:
    outcome = await service.investigate(alert_id)

    with pytest.raises(QaSampleNotFoundError):
        await service.record_qa_review(outcome.investigation_id, QA)
    with pytest.raises(InvestigationNotFoundError):
        await service.record_qa_review(999999, QA)

    assert rows(db, "SELECT count(*) FROM review.qa_reviews") == [(0,)]


@pytest.mark.anyio
async def test_uninvestigated_alerts_of_a_day(db: Database, alert_id: int, service: InvestigationService) -> None:
    same_day = add_alert(db, date(2022, 9, 2), account="A2")
    add_alert(db, date(2022, 9, 4))
    await service.investigate(alert_id)

    assert await service.uninvestigated_alerts(date(2022, 9, 2)) == [same_day]
    assert await service.uninvestigated_alerts(date(2022, 9, 2), limit=0) == []


@pytest.mark.anyio
async def test_unknown_alert_raises_and_stores_nothing(
    db: Database, alert_id: int, service: InvestigationService, pages: ReviewPages
) -> None:
    with pytest.raises(AlertNotFoundError):
        await service.investigate(alert_id + 1000)
    with pytest.raises(AlertNotFoundError):
        await pages.alert_page(alert_id + 1000)

    assert rows(db, "SELECT count(*) FROM review.investigations") == [(0,)]


@pytest.mark.anyio
async def test_unknown_investigation_raises(db: Database, service: InvestigationService) -> None:
    with pytest.raises(InvestigationNotFoundError):
        await service.record_decision(999999, REVIEW)

    assert rows(db, "SELECT count(*) FROM review.decisions") == [(0,)]
