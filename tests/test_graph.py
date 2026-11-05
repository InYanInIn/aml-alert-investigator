import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import date, datetime
from typing import Any

import pytest
from langchain.mcp import MCPAdapter
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import ToolMessage
from langchain_core.runnables import RunnableConfig
from langgraph.types import Command

from aml_investigator.core.db import open_app_pool
from aml_investigator.investigation.agent.conversation import PROMPT_VERSION
from aml_investigator.investigation.agent.graph import InvestigationGraph, build_graph, postgres_checkpointer
from aml_investigator.investigation.agent.loop import LOOP_TOOLS, NOT_EXECUTED
from aml_investigator.investigation.agent.state import AlertRuleHit, InvestigatedAlert, InvestigationState
from aml_investigator.investigation.decision import AnalystReview, Claim, Decision, Fact, TypologyVerdict
from aml_investigator.investigation.models.rule_score import RuleScoreChatModel
from aml_investigator.investigation.routing import qa_sampled
from aml_investigator.investigation.verify import Provenance, Verification
from aml_investigator.mcp_server.server import build_server
from aml_investigator.review.repository import ReviewRepository
from tests.conftest import Database
from tests.scripted_model import ScriptedChatModel, decision_reply, text_reply, tool_reply

REVIEW = {"analyst": "jan", "final_decision": "close", "comment": "known payroll pattern"}
EVIDENCE_TOOLS = [
    "get_alert",
    "get_account_profile",
    "get_counterparties",
    "list_transactions",
    "get_transactions",
    "get_typology_candidates",
    "trace_funds",
    "trace_funds",
]
REGULATION_SEARCHES = 4


pytestmark = pytest.mark.usefixtures("regulations")


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def add_alert(db: Database, alert_date: date, window: tuple[str, str], evidence: list[int]) -> int:
    with db.owner() as conn:
        row = conn.execute(
            "INSERT INTO core.alerts (account_id, alert_date, window_start, window_end) "
            "VALUES ('A1', %s, %s, %s) RETURNING alert_id",
            (alert_date, *window),
        ).fetchone()
        assert row is not None
        created = int(str(row[0]))
        conn.execute("INSERT INTO core.alert_rule_hits VALUES (%s, 'R2_fan_out', 2.0, %s)", (created, evidence))
    return created


@pytest.fixture
def alert_id(db: Database) -> int:
    with db.owner() as conn:
        for account in ("A1", "A2", "A3"):
            conn.execute("INSERT INTO core.accounts VALUES (%s, 1, 'Bank', %s, 'Corporation')", (account, account))
        for txn_id, ts, receiver, amount in (
            (11, datetime(2022, 9, 2, 10, 15), "A2", 2848.96),
            (12, datetime(2022, 9, 2, 11, 0), "A3", 1500.0),
            (13, datetime(2022, 9, 6, 9, 0), "A2", 999.0),
        ):
            conn.execute(
                "INSERT INTO core.transactions VALUES (%s, %s, 'A1', %s, %s, 'Euro', %s, 'Euro', 'ACH', %s)",
                (txn_id, ts, receiver, amount, amount, amount),
            )
    return add_alert(db, date(2022, 9, 2), ("2022-09-01", "2022-09-03"), [11, 12])


@pytest.fixture
def later_alert_id(db: Database, alert_id: int) -> int:  # noqa: ARG001
    return add_alert(db, date(2022, 9, 6), ("2022-09-05", "2022-09-07"), [13])


def valid_decision(recommendation: str = "escalate") -> Decision:
    return Decision.model_validate(
        {
            "recommendation": recommendation,
            "typologies": [TypologyVerdict(typology="fan_out", verdict="supported", txn_ids=[11, 12])],
            "who": [],
            "what": [
                Claim(
                    text="The account paid 2,848.96 Euro on 2022-09-02.",
                    txn_ids=[11],
                    passage_ids=[],
                    facts=[
                        Fact(txn_id=11, field="amount_paid", value="2848.96"),
                        Fact(txn_id=11, field="paid_currency", value="Euro"),
                        Fact(txn_id=11, field="date", value="2022-09-02"),
                    ],
                )
            ],
            "when": [],
            "where": [],
            "why": [
                Claim(
                    text="Two payments left the account within an hour.",
                    txn_ids=[11, 12],
                    passage_ids=["AMLR art. 69(1)"],
                    facts=[],
                )
            ],
            "how": [],
        }
    )


def citing(txn_id: int, recommendation: str = "escalate") -> Decision:
    decision = valid_decision(recommendation)
    decision.why[0].txn_ids.append(txn_id)
    return decision


def thread(alert_id: int) -> RunnableConfig:
    return {"configurable": {"thread_id": f"{alert_id}:{uuid.uuid4().hex}"}}


@asynccontextmanager
async def running_graph(db: Database, model: BaseChatModel, **limits: int) -> AsyncIterator[InvestigationGraph]:
    async with open_app_pool(db.app_dsn) as pool:
        yield build_graph(
            mcp=MCPAdapter(build_server(db.mcp_dsn)),
            model=model,
            repository=ReviewRepository(pool),
            checkpointer=await postgres_checkpointer(pool),
            **limits,
        )


def investigations(db: Database) -> list[tuple[Any, ...]]:
    with db.owner() as conn:
        return conn.execute(
            "SELECT investigation_id, thread_id, model, prompt_version, recommendation, verification, route, trace "
            "FROM review.investigations ORDER BY investigation_id"
        ).fetchall()


def decisions(db: Database) -> list[tuple[Any, ...]]:
    with db.owner() as conn:
        return conn.execute(
            "SELECT investigation_id, analyst, final_decision, comment FROM review.decisions"
        ).fetchall()


def loop_calls(trace: dict[str, Any]) -> list[dict[str, Any]]:
    return [call for call in trace["tool_calls"] if call["phase"] == "loop"]


def scripted(*replies: Any) -> ScriptedChatModel:
    return ScriptedChatModel(replies=list(replies))


@pytest.mark.anyio
async def test_valid_path_stops_at_review_with_a_traced_investigation(db: Database, alert_id: int) -> None:
    model = scripted(text_reply("Nothing to follow up.", tokens=30), decision_reply(valid_decision(), tokens=70))
    config = thread(alert_id)
    async with running_graph(db, model) as graph:
        result = await graph.ainvoke({"alert_id": alert_id}, config)

    [(investigation_id, thread_id, name, prompt_version, recommendation, verification, route, trace)] = investigations(
        db
    )
    assert result["__interrupt__"][0].value == {
        "investigation_id": investigation_id,
        "recommendation": "escalate",
        "verification_status": "passed",
    }
    assert thread_id == config["configurable"]["thread_id"]
    assert (name, prompt_version, recommendation, route) == ("scripted", PROMPT_VERSION, "escalate", "escalated")
    assert verification == {"status": "passed", "errors": []}
    assert (trace["loop_stop"], trace["tokens"], trace["repairs"]) == ("model_done", 100, 0)
    assert trace["verifications"] == [{"status": "passed", "errors": []}]
    phases = [(call["phase"], call["tool"]) for call in trace["tool_calls"]]
    assert phases == [
        *(("evidence", tool) for tool in EVIDENCE_TOOLS),
        *[("regulations", "search_regulations")] * REGULATION_SEARCHES,
    ]
    rows = {call["tool"]: call["rows"] for call in trace["tool_calls"] if call["phase"] == "evidence"}
    assert (rows["list_transactions"], rows["get_transactions"], rows["get_typology_candidates"]) == (2, 2, 1)
    assert decisions(db) == []

    investigate, draft = model.calls
    assert set(investigate.tool_names) == set(LOOP_TOOLS)
    assert draft.drafting
    assert {message.name for message in draft.messages if isinstance(message, ToolMessage)} >= {
        "get_alert",
        "trace_funds",
        "search_regulations",
    }


@pytest.mark.anyio
async def test_resume_records_exactly_one_decision(db: Database, alert_id: int) -> None:
    config = thread(alert_id)
    async with running_graph(db, scripted(text_reply("done"), decision_reply(valid_decision()))) as graph:
        await graph.ainvoke({"alert_id": alert_id}, config)
        final = await graph.ainvoke(Command(resume=REVIEW), config)

    [(investigation_id, *_)] = investigations(db)
    assert decisions(db) == [(investigation_id, "jan", "close", "known payroll pattern")]
    assert final["review"] == AnalystReview.model_validate(REVIEW)
    assert "__interrupt__" not in final


@pytest.mark.anyio
async def test_invalid_citation_is_repaired(db: Database, alert_id: int) -> None:
    model = scripted(text_reply("done"), decision_reply(citing(999)), decision_reply(valid_decision()))
    async with running_graph(db, model) as graph:
        result = await graph.ainvoke({"alert_id": alert_id}, thread(alert_id))

    [(*_, recommendation, verification, route, trace)] = investigations(db)
    assert (recommendation, verification["status"], route, trace["repairs"]) == ("escalate", "passed", "escalated", 1)
    assert trace["verifications"] == [
        {"status": "failed", "errors": ["transaction 999 was not returned by any tool"]},
        {"status": "passed", "errors": []},
    ]
    assert result["__interrupt__"][0].value["verification_status"] == "passed"
    repair_request = model.calls[-1].messages[-1]
    assert model.calls[-1].drafting
    assert "transaction 999 was not returned by any tool" in repair_request.text
    assert "999" in repair_request.text


@pytest.mark.anyio
async def test_second_failure_is_stored_as_failed_and_escalated(db: Database, alert_id: int) -> None:
    model = scripted(text_reply("done"), decision_reply(citing(999, "close")), decision_reply(citing(998, "close")))
    async with running_graph(db, model) as graph:
        result = await graph.ainvoke({"alert_id": alert_id}, thread(alert_id))

    [(*_, recommendation, verification, route, trace)] = investigations(db)
    assert result["decision"].recommendation == "close"
    assert (recommendation, route, trace["repairs"]) == ("escalate", "escalated", 1)
    assert verification == {"status": "failed", "errors": ["transaction 998 was not returned by any tool"]}
    assert result["__interrupt__"][0].value["verification_status"] == "failed"
    assert decisions(db) == []


@pytest.mark.anyio
async def test_unparseable_output_is_repaired(db: Database, alert_id: int) -> None:
    model = scripted(text_reply("done"), text_reply("I would escalate this."), decision_reply(valid_decision()))
    async with running_graph(db, model) as graph:
        await graph.ainvoke({"alert_id": alert_id}, thread(alert_id))

    [(*_, verification, _route, trace)] = investigations(db)
    assert (verification["status"], trace["repairs"]) == ("passed", 1)
    first_attempt = trace["verifications"][0]
    assert first_attempt["status"] == "failed"
    assert first_attempt["errors"][0].startswith("output does not parse into a Decision")
    repair_request = model.calls[-1].messages[-1].text
    assert "output does not parse into a Decision" in repair_request
    assert "I would escalate this." in repair_request


@pytest.mark.anyio
async def test_no_decision_at_all_stores_an_empty_escalation(db: Database, alert_id: int) -> None:
    invalid = tool_reply(("Decision", {"recommendation": "maybe"}))
    model = scripted(text_reply("done"), text_reply("no idea"), invalid)
    async with running_graph(db, model) as graph:
        result = await graph.ainvoke({"alert_id": alert_id}, thread(alert_id))

    [(*_, recommendation, verification, route, _trace)] = investigations(db)
    assert (recommendation, route, verification["status"]) == ("escalate", "escalated", "failed")
    [error] = verification["errors"]
    assert error.startswith("output does not parse into a Decision")
    assert result["decision"].model_dump() == {
        "recommendation": "escalate",
        "typologies": [],
        **{field: [] for field in ("who", "what", "when", "where", "why", "how")},
    }


@pytest.mark.anyio
async def test_loop_stops_at_the_tool_call_limit(db: Database, alert_id: int) -> None:
    calls = [("get_account_profile", {"account_id": account}) for account in ("A1", "A2", "A3")]
    model = scripted(tool_reply(*calls), decision_reply(valid_decision()))
    async with running_graph(db, model, max_tool_calls=2) as graph:
        await graph.ainvoke({"alert_id": alert_id}, thread(alert_id))

    [(*_, trace)] = investigations(db)
    assert trace["loop_stop"] == "tool_call_limit"
    assert [call["args"]["account_id"] for call in loop_calls(trace)] == ["A1", "A2"]
    draft = model.calls[-1]
    assert draft.drafting
    answers = [message for message in draft.messages if isinstance(message, ToolMessage)]
    assert answers[-REGULATION_SEARCHES - 1].text == NOT_EXECUTED


@pytest.mark.anyio
async def test_loop_stops_at_the_token_budget(db: Database, alert_id: int) -> None:
    profile = ("get_account_profile", {"account_id": "A2"})
    model = scripted(tool_reply(profile, tokens=60), tool_reply(profile, tokens=60), decision_reply(valid_decision()))
    async with running_graph(db, model, token_budget=100) as graph:
        await graph.ainvoke({"alert_id": alert_id}, thread(alert_id))

    [(*_, trace)] = investigations(db)
    assert (trace["loop_stop"], trace["tokens"], len(loop_calls(trace))) == ("token_budget", 120, 1)
    assert len(model.calls) == 3


@pytest.mark.anyio
async def test_zero_tool_calls_skips_the_loop(db: Database, alert_id: int) -> None:
    model = scripted(decision_reply(valid_decision()))
    async with running_graph(db, model, max_tool_calls=0) as graph:
        await graph.ainvoke({"alert_id": alert_id}, thread(alert_id))

    [(*_, verification, _route, trace)] = investigations(db)
    assert (verification["status"], trace["loop_stop"], loop_calls(trace)) == ("passed", "skipped", [])
    [only] = model.calls
    assert only.drafting


@pytest.mark.anyio
async def test_tool_errors_go_back_to_the_model(db: Database, alert_id: int) -> None:
    model = scripted(
        tool_reply(("get_regulation_passage", {"passage_id": "nope"}), ("get_alert", {})),
        text_reply("done"),
        decision_reply(valid_decision()),
    )
    async with running_graph(db, model) as graph:
        await graph.ainvoke({"alert_id": alert_id}, thread(alert_id))

    [(*_, trace)] = investigations(db)
    assert [(call["tool"], "error" in call) for call in loop_calls(trace)] == [
        ("get_regulation_passage", True),
        ("get_alert", True),
    ]
    missing, unavailable = (message for message in model.calls[1].messages[-2:] if isinstance(message, ToolMessage))
    assert (missing.status, unavailable.status) == ("error", "error")
    assert "Passage nope does not exist" in missing.text
    assert unavailable.text == "Tool get_alert is not available."


@pytest.mark.anyio
async def test_model_cannot_choose_the_alert(db: Database, alert_id: int, later_alert_id: int) -> None:
    peek = ("list_transactions", {"account_id": "A1", "alert_id": later_alert_id})
    model = scripted(tool_reply(peek), text_reply("done"), decision_reply(valid_decision()))
    async with running_graph(db, model) as graph:
        await graph.ainvoke({"alert_id": alert_id}, thread(alert_id))

    schemas = {tool["function"]["name"]: tool["function"]["parameters"] for tool in model.calls[0].tools}
    assert all("alert_id" not in schema["properties"] for schema in schemas.values())
    assert all("alert_id" not in schema.get("required", []) for schema in schemas.values())
    [(*_, trace)] = investigations(db)
    [call] = loop_calls(trace)
    assert call["args"]["alert_id"] == alert_id
    answer = model.calls[1].messages[-1]
    assert isinstance(answer, ToolMessage)
    assert [txn["txn_id"] for txn in answer.artifact["structured_content"]["transactions"]] == [11, 12]


@pytest.mark.anyio
async def test_verified_close_is_auto_closed_without_interrupt(db: Database, alert_id: int) -> None:
    async with running_graph(db, scripted(text_reply("done"), decision_reply(valid_decision("close")))) as graph:
        result = await graph.ainvoke({"alert_id": alert_id}, thread(alert_id))

    [(investigation_id, *_, recommendation, _verification, route, _trace)] = investigations(db)
    expected = "qa_sample" if qa_sampled("A1", date(2022, 9, 2)) else "auto_closed"
    assert (recommendation, route, result["route"]) == ("close", expected, expected)
    assert "__interrupt__" not in result
    assert decisions(db) == [(investigation_id, "auto-close", "close", "")]


@pytest.mark.anyio
async def test_reinvoking_a_thread_keeps_one_investigation(db: Database, alert_id: int) -> None:
    config = thread(alert_id)
    model = scripted(*[text_reply("done"), decision_reply(valid_decision())] * 2)
    async with running_graph(db, model) as graph:
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
async def test_resume_after_rebuilding_pool_and_graph(db: Database, alert_id: int) -> None:
    config = thread(alert_id)
    async with running_graph(db, scripted(text_reply("done"), decision_reply(valid_decision()))) as graph:
        await graph.ainvoke({"alert_id": alert_id}, config)

    async with running_graph(db, scripted()) as restarted:
        final = await restarted.ainvoke(Command(resume=REVIEW), config)

    [(investigation_id, *_)] = investigations(db)
    assert final["investigation_id"] == investigation_id
    assert decisions(db) == [(investigation_id, "jan", "close", "known payroll pattern")]


@pytest.mark.anyio
async def test_checkpointer_restores_every_typed_state_field(db: Database) -> None:
    state: InvestigationState = {
        "alert": InvestigatedAlert(
            account_id="A1",
            alert_date=date(2022, 9, 2),
            as_of=datetime(2022, 9, 3),
            rule_hits=[AlertRuleHit(rule_id="R2_fan_out", evidence_txn_ids=[11, 12])],
        ),
        "provenance": Provenance(
            transactions={11: {"txn_id": 11, "ts": "2022-09-02T10:15:00", "amount_paid": 2848.96}},
            passages={"AMLR art. 69(1)": {"passage_id": "AMLR art. 69(1)", "text": "report", "content_sha256": "x"}},
        ),
        "draft": valid_decision(),
        "verification": Verification(status="failed", errors=["transaction 999 was not returned by any tool"]),
        "decision": valid_decision("close"),
        "review": AnalystReview.model_validate(REVIEW),
    }
    async with open_app_pool(db.app_dsn) as pool:
        serde = (await postgres_checkpointer(pool)).serde

    restored = serde.loads_typed(serde.dumps_typed(state))

    assert {key: type(value) for key, value in restored.items()} == {key: type(value) for key, value in state.items()}
    assert restored == state


@pytest.mark.anyio
@pytest.mark.parametrize(("close_below", "route"), [(1.0, "escalated"), (3.0, "auto_closed")])
async def test_rule_score_model_decisions_pass_verification(
    db: Database, alert_id: int, regulations: list[str], close_below: float, route: str
) -> None:
    model = RuleScoreChatModel(thresholds={"R2_fan_out": 1.0}, close_below=close_below)
    async with running_graph(db, model) as graph:
        result = await graph.ainvoke({"alert_id": alert_id}, thread(alert_id))

    [(*_, name, _version, _recommendation, verification, stored_route, trace)] = investigations(db)
    expected = "qa_sample" if route == "auto_closed" and qa_sampled("A1", date(2022, 9, 2)) else route
    assert (name, verification, stored_route) == ("rule-score", {"status": "passed", "errors": []}, expected)
    assert (trace["loop_stop"], trace["repairs"]) == ("model_done", 0)
    cited = {passage for claim in result["decision"].why for passage in claim.passage_ids}
    assert cited == {"AMLR art. 69(1)", "PL-AML art. 74 ust. 1", "AMLR art. 34(2)"} <= set(regulations)
