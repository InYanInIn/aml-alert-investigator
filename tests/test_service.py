from collections.abc import AsyncIterator
from datetime import datetime
from typing import Any

import pytest
from langchain.mcp import MCPAdapter

from aml_investigator.core.db import open_app_pool
from aml_investigator.investigation.decision import AnalystReview
from aml_investigator.investigation.drafters import scripted_drafter
from aml_investigator.investigation.graph import build_graph, postgres_checkpointer
from aml_investigator.investigation.repository import ReviewRepository
from aml_investigator.investigation.service import AlertNotFoundError, InvestigationNotFoundError, InvestigationService
from aml_investigator.mcp_server.server import build_server
from tests.conftest import Database

REVIEW = AnalystReview(analyst="jan", final_decision="close", comment="known payroll pattern")


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


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
        row = conn.execute(
            "INSERT INTO core.alerts (account_id, alert_date, window_start, window_end) "
            "VALUES ('A1', '2022-09-02', '2022-09-01', '2022-09-03') RETURNING alert_id"
        ).fetchone()
        assert row is not None
        created = int(str(row[0]))
        conn.execute("INSERT INTO core.alert_rule_hits VALUES (%s, 'R2_fan_out', 2.0, ARRAY[11, 12])", (created,))
    return created


@pytest.fixture
async def service(db: Database) -> AsyncIterator[InvestigationService]:
    async with open_app_pool(db.app_dsn) as pool, MCPAdapter(build_server(db.mcp_dsn)) as mcp:
        repository = ReviewRepository(pool)
        graph = build_graph(
            mcp=mcp, drafter=scripted_drafter, repository=repository, checkpointer=await postgres_checkpointer(pool)
        )
        yield InvestigationService(graph=graph, repository=repository, mcp=mcp)


def rows(db: Database, query: str) -> list[tuple[Any, ...]]:
    with db.owner() as conn:
        return conn.execute(query).fetchall()


@pytest.mark.anyio
async def test_investigate_then_decide_without_http(db: Database, alert_id: int, service: InvestigationService) -> None:
    outcome = await service.investigate(alert_id)

    [(investigation_id, stored_alert_id, thread_id, recommendation)] = rows(
        db, "SELECT investigation_id, alert_id, thread_id, recommendation FROM review.investigations"
    )
    assert outcome.investigation_id == investigation_id
    assert (outcome.recommendation, outcome.verification_status) == ("escalate", "passed")
    assert (stored_alert_id, recommendation) == (alert_id, "escalate")
    assert thread_id.startswith(f"{alert_id}:")

    assert await service.record_decision(investigation_id, REVIEW) == alert_id

    assert rows(db, "SELECT investigation_id, analyst, final_decision, comment FROM review.decisions") == [
        (investigation_id, "jan", "close", "known payroll pattern")
    ]
    page = await service.alert_page(alert_id)
    assert page.investigation is not None
    assert page.investigation.review is not None
    assert page.investigation.review.final_decision == "close"
    assert [txn["txn_id"] for txn in page.evidence["transactions"]] == [11, 12]


@pytest.mark.anyio
async def test_second_decision_is_ignored(db: Database, alert_id: int, service: InvestigationService) -> None:
    outcome = await service.investigate(alert_id)
    await service.record_decision(outcome.investigation_id, REVIEW)

    override = AnalystReview(analyst="eve", final_decision="escalate", comment="second opinion")
    assert await service.record_decision(outcome.investigation_id, override) == alert_id

    assert rows(db, "SELECT analyst, final_decision FROM review.decisions") == [("jan", "close")]


@pytest.mark.anyio
async def test_each_investigation_gets_a_fresh_thread(
    db: Database, alert_id: int, service: InvestigationService
) -> None:
    first = await service.investigate(alert_id)
    second = await service.investigate(alert_id)

    assert first.investigation_id != second.investigation_id
    [(threads,)] = rows(db, "SELECT count(DISTINCT thread_id) FROM review.investigations")
    assert threads == 2


@pytest.mark.anyio
async def test_unknown_alert_raises_and_stores_nothing(
    db: Database, alert_id: int, service: InvestigationService
) -> None:
    with pytest.raises(AlertNotFoundError):
        await service.investigate(alert_id + 1000)
    with pytest.raises(AlertNotFoundError):
        await service.alert_page(alert_id + 1000)

    assert rows(db, "SELECT count(*) FROM review.investigations") == [(0,)]


@pytest.mark.anyio
async def test_unknown_investigation_raises(db: Database, service: InvestigationService) -> None:
    with pytest.raises(InvestigationNotFoundError):
        await service.record_decision(999999, REVIEW)

    assert rows(db, "SELECT count(*) FROM review.decisions") == [(0,)]
