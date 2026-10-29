import uuid
from datetime import datetime
from typing import Any

import pytest
from langchain.mcp import MCPAdapter
from langchain_core.runnables import RunnableConfig
from langgraph.types import Command

from aml_investigator.agent.decision import Decision
from aml_investigator.agent.drafters import Drafter, EvidencePack, scripted_drafter
from aml_investigator.agent.graph import InvestigationGraph, build_graph, open_checkpointer
from aml_investigator.mcp_server import build_server
from tests.conftest import Database

REVIEW = {"analyst": "jan", "final_decision": "close", "comment": "known payroll pattern"}


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
                "INSERT INTO core.transactions VALUES (%s, %s, 'A1', %s, %s, 'Euro', %s, 'Euro', 'ACH')",
                (txn_id, ts, receiver, amount, amount),
            )
        row = conn.execute(
            "INSERT INTO core.alerts (account_id, alert_date, window_start, window_end) "
            "VALUES ('A1', '2022-09-02', '2022-09-01', '2022-09-03') RETURNING alert_id"
        ).fetchone()
        assert row is not None
        created = int(str(row[0]))
        conn.execute("INSERT INTO core.alert_rule_hits VALUES (%s, 'R2_fan_out', 2.0, ARRAY[11, 12])", (created,))
    return created


def thread(alert_id: int) -> RunnableConfig:
    return {"configurable": {"thread_id": f"{alert_id}:{uuid.uuid4().hex}"}}


def graph_for(db: Database, checkpointer: Any, drafter: Drafter = scripted_drafter) -> InvestigationGraph:
    return build_graph(
        mcp=MCPAdapter(build_server(db.mcp_dsn)), drafter=drafter, app_dsn=db.app_dsn, checkpointer=checkpointer
    )


def investigations(db: Database) -> list[tuple[Any, ...]]:
    with db.owner() as conn:
        return conn.execute(
            "SELECT investigation_id, thread_id, model, prompt_version, recommendation, verification "
            "FROM review.investigations ORDER BY investigation_id"
        ).fetchall()


def decisions(db: Database) -> list[tuple[Any, ...]]:
    with db.owner() as conn:
        return conn.execute(
            "SELECT investigation_id, analyst, final_decision, comment FROM review.decisions"
        ).fetchall()


async def citing_unseen_transaction(evidence: EvidencePack) -> Decision:
    drafted = await scripted_drafter(evidence)
    drafted.what[0].txn_ids.append(999)
    return drafted.model_copy(update={"recommendation": "close"})


@pytest.mark.anyio
async def test_run_stops_at_review_with_one_verified_investigation(db: Database, alert_id: int) -> None:
    config = thread(alert_id)
    async with open_checkpointer(db.app_dsn) as checkpointer:
        result = await graph_for(db, checkpointer).ainvoke({"alert_id": alert_id}, config)

    [(investigation_id, thread_id, model, prompt_version, recommendation, verification)] = investigations(db)
    assert result["__interrupt__"][0].value == {
        "investigation_id": investigation_id,
        "recommendation": "escalate",
        "verification_status": "passed",
    }
    assert thread_id == config["configurable"]["thread_id"]
    assert (model, prompt_version, recommendation) == ("scripted", "m1", "escalate")
    assert verification == {"status": "passed", "errors": []}
    assert decisions(db) == []


@pytest.mark.anyio
async def test_resume_records_exactly_one_decision(db: Database, alert_id: int) -> None:
    config = thread(alert_id)
    async with open_checkpointer(db.app_dsn) as checkpointer:
        graph = graph_for(db, checkpointer)
        await graph.ainvoke({"alert_id": alert_id}, config)
        final = await graph.ainvoke(Command(resume=REVIEW), config)

    [(investigation_id, *_)] = investigations(db)
    assert decisions(db) == [(investigation_id, "jan", "close", "known payroll pattern")]
    assert final["review"] == REVIEW
    assert "__interrupt__" not in final


@pytest.mark.anyio
async def test_unseen_citation_fails_verification_and_forces_escalation(db: Database, alert_id: int) -> None:
    async with open_checkpointer(db.app_dsn) as checkpointer:
        result = await graph_for(db, checkpointer, citing_unseen_transaction).ainvoke(
            {"alert_id": alert_id}, thread(alert_id)
        )

    [(*_, recommendation, verification)] = investigations(db)
    assert result["decision"]["recommendation"] == "close"
    assert recommendation == "escalate"
    assert verification == {"status": "failed", "errors": ["transaction 999 was not returned by any tool"]}
    assert result["__interrupt__"][0].value["verification_status"] == "failed"


@pytest.mark.anyio
async def test_reinvoking_a_thread_keeps_one_investigation(db: Database, alert_id: int) -> None:
    config = thread(alert_id)
    async with open_checkpointer(db.app_dsn) as checkpointer:
        graph = graph_for(db, checkpointer)
        first = await graph.ainvoke({"alert_id": alert_id}, config)
        second = await graph.ainvoke({"alert_id": alert_id}, config)
        persist_steps = [
            snapshot
            async for snapshot in graph.aget_state_history(config)
            if snapshot.next == ("persist_investigation",)
        ]

    [(investigation_id, *_)] = investigations(db)
    assert len(persist_steps) == 2
    assert first["investigation_id"] == second["investigation_id"] == investigation_id


@pytest.mark.anyio
async def test_resume_from_fresh_graph_on_same_checkpointer(db: Database, alert_id: int) -> None:
    config = thread(alert_id)
    async with open_checkpointer(db.app_dsn) as checkpointer:
        await graph_for(db, checkpointer).ainvoke({"alert_id": alert_id}, config)

    async with open_checkpointer(db.app_dsn) as restarted:
        final = await graph_for(db, restarted).ainvoke(Command(resume=REVIEW), config)

    [(investigation_id, *_)] = investigations(db)
    assert final["investigation_id"] == investigation_id
    assert decisions(db) == [(investigation_id, "jan", "close", "known payroll pattern")]
