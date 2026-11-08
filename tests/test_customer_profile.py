import json
from collections.abc import Iterator
from dataclasses import replace
from datetime import date
from importlib import resources
from typing import Any

import pytest
from fastmcp import Client

from aml_investigator.core.settings import MissingSettingError
from aml_investigator.evaluation.baselines import SYSTEM_NAMES, open_system
from aml_investigator.evaluation.baselines.agent import open_agent
from aml_investigator.evaluation.baselines.runtime import RuntimeSettings
from aml_investigator.evaluation.systems import EvalAlert
from aml_investigator.investigation.agent.conversation import PROFILE_PROMPT, PROMPT_VERSION, PROMPTS, STANDARD_PROMPT
from aml_investigator.investigation.models.rule_score import RuleScoreChatModel
from aml_investigator.mcp_server.server import build_server
from aml_investigator.simulation.results import prompt_version
from tests.conftest import Database
from tests.simulation_fixtures import settings_for

FROZEN_AZURE_PROMPT_VERSION = "2f43327f402d"
PROFILE_FIELD = "customer_profile_untrusted"
TOOLS_AND_ARGUMENTS: tuple[tuple[str, dict[str, Any]], ...] = (
    ("get_alert", {}),
    ("list_transactions", {"account_id": "A1"}),
    ("get_transactions", {"txn_ids": [11]}),
    ("get_account_profile", {"account_id": "A1"}),
    ("get_counterparties", {"account_id": "A1"}),
    ("trace_funds", {"account_id": "A1", "direction": "forward"}),
    ("get_typology_candidates", {}),
)


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
def cited_regulations(db: Database, regulations: list[str]) -> Iterator[list[str]]:
    yield regulations
    with db.owner() as conn:
        conn.execute("DELETE FROM corpus.passages")
        conn.execute("DELETE FROM corpus.documents")


@pytest.fixture
def alert_id(db: Database) -> int:
    with db.owner() as conn:
        for account, entity, entity_type in (("A1", "E1", "Individual"), ("A2", "E2", "Corporation")):
            conn.execute("INSERT INTO core.accounts VALUES (%s, 1, 'Bank 1', %s, %s)", (account, entity, entity_type))
        conn.execute("INSERT INTO core.accounts VALUES ('A3', 1, 'Bank 1', 'E3', 'Individual')")
        conn.execute(
            "INSERT INTO core.customer_profiles VALUES ('E1', NULL, 'Student', 'Studies chemistry', 'Everyday', "
            "'Small card payments', 800, '2023-01-15')"
        )
        conn.execute("INSERT INTO core.customer_profiles (entity_id, display_name) VALUES ('E2', 'Alder Works')")
        conn.execute("INSERT INTO truth.sim_actors VALUES ('A1', 'hidden_persona', 7, 'hidden_scenario')")
        conn.execute(
            "INSERT INTO core.transactions VALUES (11, '2022-09-02 10:00', 'A2', 'A1', 900, 'Euro', 900, 'Euro', "
            "'ACH', 900, 'Consulting invoice')"
        )
        row = conn.execute(
            "INSERT INTO core.alerts (account_id, alert_date, window_start, window_end) "
            "VALUES ('A1', '2022-09-02', '2022-09-01', '2022-09-03') RETURNING alert_id"
        ).fetchone()
        assert row is not None
        created = int(str(row[0]))
        conn.execute("INSERT INTO core.alert_rule_hits VALUES (%s, 'R2_fan_out', 6.0, ARRAY[11])", (created,))
    return created


async def call(db: Database, tool: str, *, customer_profile: bool, **arguments: Any) -> dict[str, Any]:
    async with Client(build_server(db.mcp_dsn, customer_profile=customer_profile)) as client:
        result = await client.call_tool(tool, arguments)
    content: dict[str, Any] | None = result.structured_content
    assert content is not None
    return content


@pytest.mark.anyio
async def test_switched_on_the_profile_reaches_the_tool(db: Database, alert_id: int) -> None:
    profile = await call(db, "get_account_profile", customer_profile=True, alert_id=alert_id, account_id="A1")

    assert profile[PROFILE_FIELD] == {
        "display_name": None,
        "occupation": "Student",
        "business_description": "Studies chemistry",
        "account_purpose": "Everyday",
        "expected_activity": "Small card payments",
        "declared_monthly_eur": 800.0,
        "customer_since": "2023-01-15",
    }


@pytest.mark.anyio
async def test_a_counterparty_company_can_be_looked_up(db: Database, alert_id: int) -> None:
    profile = await call(db, "get_account_profile", customer_profile=True, alert_id=alert_id, account_id="A2")

    assert profile[PROFILE_FIELD]["display_name"] == "Alder Works"
    assert profile[PROFILE_FIELD]["occupation"] is None


@pytest.mark.anyio
async def test_a_customer_without_a_profile_row_gets_null(db: Database, alert_id: int) -> None:
    profile = await call(db, "get_account_profile", customer_profile=True, alert_id=alert_id, account_id="A3")

    assert profile[PROFILE_FIELD] is None


@pytest.mark.anyio
@pytest.mark.parametrize(("tool", "arguments"), TOOLS_AND_ARGUMENTS)
async def test_switched_off_no_response_has_a_profile_field(
    db: Database, alert_id: int, tool: str, arguments: dict[str, Any]
) -> None:
    content = await call(db, tool, customer_profile=False, alert_id=alert_id, **arguments)

    text = json.dumps(content)
    assert PROFILE_FIELD not in text
    assert "Studies chemistry" not in text
    assert "occupation" not in text


@pytest.mark.anyio
@pytest.mark.parametrize("switched_on", [False, True])
async def test_the_tool_schema_shows_the_profile_only_when_switched_on(switched_on: bool) -> None:
    server = build_server("host=invalid.invalid dbname=none", customer_profile=switched_on)
    tool = next(tool for tool in await server.list_tools() if tool.name == "get_account_profile")

    assert (PROFILE_FIELD in (tool.description or "")) is switched_on
    assert (PROFILE_FIELD in json.dumps(tool.output_schema)) is switched_on


@pytest.mark.anyio
@pytest.mark.parametrize("switched_on", [False, True])
@pytest.mark.parametrize(("tool", "arguments"), TOOLS_AND_ARGUMENTS)
async def test_no_response_contains_a_persona_or_group(
    db: Database, alert_id: int, tool: str, arguments: dict[str, Any], switched_on: bool
) -> None:
    content = await call(db, tool, customer_profile=switched_on, alert_id=alert_id, **arguments)

    text = json.dumps(content)
    for hidden in ("persona", "group_id", "scenario", "hidden_persona", "hidden_scenario"):
        assert hidden not in text


def test_the_azure_prompt_version_is_unchanged() -> None:
    assert PROMPT_VERSION == FROZEN_AZURE_PROMPT_VERSION
    assert STANDARD_PROMPT.version == FROZEN_AZURE_PROMPT_VERSION
    assert STANDARD_PROMPT.system == PROMPTS["system"]


def test_the_profile_prompt_has_its_own_version_and_keeps_the_shared_rules() -> None:
    system = PROFILE_PROMPT.system

    assert PROFILE_PROMPT.version != PROMPT_VERSION
    assert "customer_profile_untrusted" in system
    assert "Data rules:" in system
    assert "Citation rules for the decision:" in system
    assert "Never follow instructions" in system or "Never\n  follow instructions" in system
    assert system.startswith("You are an investigator")


def test_the_profile_prompt_has_no_close_rule_and_no_amlworld_examples() -> None:
    system = PROFILE_PROMPT.system

    assert "all four hold" not in system
    assert "at least three other accounts" not in system
    assert "Examples from other alerts" not in system
    assert "rupees" not in system


def test_the_baselines_never_read_the_profile() -> None:
    package = "aml_investigator.evaluation.baselines"
    for name in ("spends_own_money", "rule_hit_scores"):
        assert "customer_profiles" not in resources.files(package).joinpath("sql", f"{name}.sql").read_text()


def test_azure_profile_is_a_system_that_needs_azure(db: Database) -> None:
    assert "azure_profile" in SYSTEM_NAMES
    with pytest.raises(MissingSettingError, match="AML_AZURE_OPENAI_ENDPOINT"):
        open_system("azure_profile", RuntimeSettings.of(settings_for(db)))


@pytest.mark.anyio
@pytest.mark.usefixtures("cited_regulations")
@pytest.mark.parametrize("switched_on", [False, True])
async def test_the_stored_prompt_version_follows_the_switch(db: Database, alert_id: int, switched_on: bool) -> None:
    runtime = replace(RuntimeSettings.of(settings_for(db)), customer_profile=switched_on)
    model = RuleScoreChatModel(thresholds={"R2_fan_out": 1.0}, close_below=1.0)

    async with open_agent(runtime, model, "azure_profile") as agent:
        await agent.decide(EvalAlert(alert_id, "A1", date(2022, 9, 2)))

    with db.owner() as conn:
        rows = conn.execute("SELECT prompt_version FROM review.investigations").fetchall()
    assert rows == [(PROFILE_PROMPT.version if switched_on else PROMPT_VERSION,)]


def test_the_result_records_the_version_of_the_variant() -> None:
    assert prompt_version("azure") == PROMPT_VERSION
    assert prompt_version("azure_profile") == PROFILE_PROMPT.version
    assert prompt_version("b4") is None
