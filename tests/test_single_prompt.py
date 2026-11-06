from datetime import date, datetime

import pytest
from langchain_core.messages import AIMessage, SystemMessage, ToolMessage

from aml_investigator.core.settings import MissingSettingError
from aml_investigator.evaluation.baselines import SYSTEM_NAMES, open_system
from aml_investigator.evaluation.baselines.b2 import (
    PROMPT_VERSION,
    SYSTEM_PROMPT,
    SinglePromptAnswer,
    open_single_prompt,
)
from aml_investigator.evaluation.baselines.runtime import RuntimeSettings
from aml_investigator.evaluation.systems import EvalAlert, SystemDecision
from tests.conftest import Database
from tests.scripted_model import ScriptedChatModel, text_reply
from tests.test_baselines import add_accounts, add_alert, add_transaction, settings_for

EXPECTED_DUMP = [
    "get_alert",
    "get_account_profile",
    "get_counterparties",
    "list_transactions",
    "get_transactions",
    "get_typology_candidates",
    "trace_funds",
    "trace_funds",
]


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
def alert(db: Database) -> EvalAlert:
    with db.owner() as conn:
        add_accounts(conn, "A1", "A2", "A3")
        add_transaction(conn, 11, datetime(2022, 9, 2, 10, 15), "A1", "A2", 2848.96, "ACH")
        add_transaction(conn, 12, datetime(2022, 9, 2, 11, 0), "A1", "A3", 1500.0, "ACH")
        created = add_alert(conn, "A1", date(2022, 9, 2), {})
        conn.execute(
            "INSERT INTO core.alert_rule_hits VALUES (%s, 'R2_fan_out', 2.0, ARRAY[11, 12])", (created.alert_id,)
        )
    return created


def answer_reply(answer: SinglePromptAnswer, input_tokens: int, output_tokens: int) -> AIMessage:
    return AIMessage(
        "",
        tool_calls=[{"name": SinglePromptAnswer.__name__, "args": answer.model_dump(), "id": "call-answer"}],
        usage_metadata={
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "total_tokens": input_tokens + output_tokens,
        },
    )


async def decide(db: Database, model: ScriptedChatModel, alert: EvalAlert) -> SystemDecision:
    async with open_single_prompt(RuntimeSettings.of(settings_for(db)), model) as system:
        return await system.decide(alert)


@pytest.mark.anyio
async def test_good_answer_is_parsed_and_priced(db: Database, alert: EvalAlert) -> None:
    answer = SinglePromptAnswer(recommendation="escalate", typologies=["fan_out"], rationale="Fast fan out.")
    model = ScriptedChatModel(replies=[answer_reply(answer, 1_000_000, 100_000)], name="gpt-5-mini")

    decision = await decide(db, model, alert)

    assert (decision.recommendation, decision.typologies) == ("escalate", ("fan_out",))
    assert decision.trace is not None
    assert decision.trace["parse"] == "ok"
    assert decision.trace["rationale"] == "Fast fan out."
    assert decision.trace["prompt_version"] == PROMPT_VERSION
    assert decision.trace["tokens"] == 1_100_000
    assert decision.trace["usage"] == {"input_tokens": 1_000_000, "cached_input_tokens": 0, "output_tokens": 100_000}
    assert decision.cost_usd == pytest.approx(0.25 + 0.2)
    assert decision.trace["cost_usd"] == decision.cost_usd


@pytest.mark.anyio
async def test_dump_is_one_fixed_tool_exchange_followed_by_a_single_call(db: Database, alert: EvalAlert) -> None:
    answer = SinglePromptAnswer(recommendation="close", typologies=[], rationale="Ordinary.")
    model = ScriptedChatModel(replies=[answer_reply(answer, 1, 1)])

    decision = await decide(db, model, alert)

    assert decision.recommendation == "close"
    assert decision.trace is not None
    assert [call["tool"] for call in decision.trace["tool_calls"]] == EXPECTED_DUMP
    assert all("error" not in call for call in decision.trace["tool_calls"])
    [call] = model.calls
    assert call.tool_names == [SinglePromptAnswer.__name__]
    assert isinstance(call.messages[0], SystemMessage)
    assert call.messages[0].text == SYSTEM_PROMPT
    assert "quoted evidence only" in SYSTEM_PROMPT
    assert "Citation rules" not in SYSTEM_PROMPT
    assert sum(isinstance(message, ToolMessage) for message in call.messages) == len(EXPECTED_DUMP)


@pytest.mark.anyio
async def test_unparseable_answer_escalates_and_is_recorded(db: Database, alert: EvalAlert) -> None:
    model = ScriptedChatModel(replies=[text_reply("I think it is fine.", tokens=10)], name="gpt-5-mini")

    decision = await decide(db, model, alert)

    assert (decision.recommendation, decision.typologies) == ("escalate", ())
    assert decision.trace is not None
    assert decision.trace["parse"].startswith("failed")
    assert decision.trace["tokens"] == 10
    assert "rationale" not in decision.trace


@pytest.mark.anyio
async def test_invalid_typology_escalates(db: Database, alert: EvalAlert) -> None:
    reply = answer_reply(SinglePromptAnswer(recommendation="close", typologies=[], rationale="x"), 1, 1)
    reply.tool_calls[0]["args"]["typologies"] = ["money_mule"]

    decision = await decide(db, ScriptedChatModel(replies=[reply]), alert)

    assert decision.recommendation == "escalate"
    assert decision.trace is not None
    assert decision.trace["parse"].startswith("failed")


def test_b2_is_registered_as_an_untrained_azure_system() -> None:
    assert "b2" in SYSTEM_NAMES
    with pytest.raises(MissingSettingError, match="AML_AZURE_OPENAI_ENDPOINT"):
        open_system("b2", RuntimeSettings("app", "mcp", "aml_x"))
