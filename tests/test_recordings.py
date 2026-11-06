import asyncio
from datetime import date, datetime
from pathlib import Path
from typing import Any

import pytest

from aml_investigator.evaluation.baselines.agent import open_agent
from aml_investigator.evaluation.baselines.runtime import RuntimeSettings
from aml_investigator.evaluation.recordings import Cassette, ReplayMismatchError, replay
from aml_investigator.evaluation.results import RECORDINGS_DIR
from aml_investigator.evaluation.systems import EvalAlert
from aml_investigator.investigation.models.rule_score import RuleScoreChatModel
from aml_investigator.review.records import Alert
from tests.conftest import Database

REPOSITORY = Path(__file__).resolve().parents[1]
COMMITTED = sorted((REPOSITORY / RECORDINGS_DIR).rglob("*.json"))
SECRET_MARKERS = ("bearer", "authorization", "api-key", "api_key", "postgresql://", "postgres://", "password", "secret")
ESCALATING = RuleScoreChatModel(thresholds={"R2_fan_out": 1.0}, close_below=1.0)
CLOSING = RuleScoreChatModel(thresholds={"R2_fan_out": 1.0}, close_below=3.0)


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def committed_cassettes() -> list[Any]:
    """Every committed cassette, or one visible skip when there is none — never a silent green."""
    if not COMMITTED:
        return [pytest.param(None, marks=pytest.mark.skip(reason=f"no cassettes under {RECORDINGS_DIR}"))]
    return [pytest.param(path, id=str(path.relative_to(REPOSITORY / RECORDINGS_DIR))) for path in COMMITTED]


def seed_alert(db: Database, alert: Alert) -> None:
    """The only rows replay needs: the alert and its account. Tool results come from the cassette."""
    with db.owner() as conn:
        conn.execute(
            "INSERT INTO core.accounts VALUES (%s, 0, 'Replay', %s, 'Replay')", (alert.account_id, alert.account_id)
        )
        conn.execute(
            "INSERT INTO core.alerts (alert_id, account_id, alert_date, window_start, window_end) "
            "OVERRIDING SYSTEM VALUE VALUES (%s, %s, %s, %s, %s)",
            (alert.alert_id, alert.account_id, alert.alert_date, alert.window_start, alert.window_end),
        )


def recorded_outcome(cassette: Cassette) -> tuple[str, str, str]:
    return (cassette.outcome.recommendation, cassette.outcome.verification_status, cassette.outcome.route)


@pytest.mark.anyio
@pytest.mark.parametrize("path", committed_cassettes())
async def test_committed_cassette_replays_to_its_recorded_outcome(db: Database, path: Path) -> None:
    cassette = Cassette.read(path)
    seed_alert(db, cassette.alert)

    outcome = await replay(cassette, app_conninfo=db.app_dsn, mcp_conninfo=db.mcp_dsn)

    assert (outcome.recommendation, outcome.verification_status, outcome.route) == recorded_outcome(cassette)


@pytest.mark.parametrize("path", committed_cassettes())
def test_committed_cassette_holds_no_secrets(path: Path) -> None:
    text = path.read_text().lower()

    assert [marker for marker in SECRET_MARKERS if marker in text] == []


@pytest.fixture
def alert_ids(db: Database) -> list[int]:
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
        created = []
        for alert_date in (date(2022, 9, 2), date(2022, 9, 3)):
            row = conn.execute(
                "INSERT INTO core.alerts (account_id, alert_date, window_start, window_end) "
                "VALUES ('A1', %s, '2022-09-01', '2022-09-04') RETURNING alert_id",
                (alert_date,),
            ).fetchone()
            assert row is not None
            created.append(int(str(row[0])))
            conn.execute(
                "INSERT INTO core.alert_rule_hits VALUES (%s, 'R2_fan_out', 2.0, ARRAY[11, 12])", (created[-1],)
            )
    return created


async def record(db: Database, alert_ids: list[int], model: RuleScoreChatModel, root: Path) -> list[Cassette]:
    settings = RuntimeSettings(db.app_dsn, db.mcp_dsn, "aml_test", recordings=root)
    async with open_agent(settings, model, "scripted") as agent:
        await asyncio.gather(*(agent.decide(EvalAlert(alert_id, "A1", date(2022, 9, 2))) for alert_id in alert_ids))
    return [Cassette.read(root / "aml_test" / "rule-score" / f"{alert_id}.json") for alert_id in alert_ids]


@pytest.mark.anyio
@pytest.mark.usefixtures("regulations")
@pytest.mark.parametrize(("model", "expected"), [(ESCALATING, "escalated"), (CLOSING, "auto_closed")])
async def test_concurrent_recordings_replay_to_the_recorded_outcome(
    db: Database, alert_ids: list[int], tmp_path: Path, model: RuleScoreChatModel, expected: str
) -> None:
    cassettes = await record(db, alert_ids, model, tmp_path)

    for alert_id, cassette in zip(alert_ids, cassettes, strict=True):
        assert cassette.alert.alert_id == alert_id
        assert cassette.model == "rule-score"
        assert [call.tool for call in cassette.tool_calls][:2] == ["get_alert", "get_account_profile"]
        assert all(call.args.get("alert_id", alert_id) == alert_id for call in cassette.tool_calls)
        assert len(cassette.model_responses) == 2
        assert cassette.outcome.route in {expected, "qa_sample"}
        outcome = await replay(cassette, app_conninfo=db.app_dsn, mcp_conninfo=db.mcp_dsn)
        assert (outcome.recommendation, outcome.verification_status, outcome.route) == recorded_outcome(cassette)


@pytest.fixture
async def cassette(db: Database, alert_ids: list[int], tmp_path: Path, regulations: list[str]) -> Cassette:  # noqa: ARG001
    [recorded] = await record(db, alert_ids[:1], ESCALATING, tmp_path)
    return recorded


@pytest.mark.anyio
async def test_replay_fails_on_a_tool_call_the_cassette_does_not_hold(db: Database, cassette: Cassette) -> None:
    truncated = cassette.model_copy(update={"tool_calls": cassette.tool_calls[:-1]})

    with pytest.raises(ReplayMismatchError, match="unrecorded tool call search_regulations"):
        await replay(truncated, app_conninfo=db.app_dsn, mcp_conninfo=db.mcp_dsn)


@pytest.mark.anyio
async def test_replay_fails_on_a_tool_call_with_other_arguments(db: Database, cassette: Cassette) -> None:
    first, *rest = cassette.tool_calls
    changed = cassette.model_copy(update={"tool_calls": [first.model_copy(update={"args": {"alert_id": -1}}), *rest]})

    with pytest.raises(ReplayMismatchError, match="differs from the recorded get_alert"):
        await replay(changed, app_conninfo=db.app_dsn, mcp_conninfo=db.mcp_dsn)


@pytest.mark.anyio
async def test_replay_fails_when_recorded_model_responses_are_left_over(db: Database, cassette: Cassette) -> None:
    padded = cassette.model_copy(update={"model_responses": [*cassette.model_responses, cassette.model_responses[0]]})

    with pytest.raises(ReplayMismatchError, match="left 1 recorded model responses and 0 recorded tool calls"):
        await replay(padded, app_conninfo=db.app_dsn, mcp_conninfo=db.mcp_dsn)


@pytest.mark.anyio
async def test_replay_fails_when_the_model_is_called_more_often_than_recorded(db: Database, cassette: Cassette) -> None:
    short = cassette.model_copy(update={"model_responses": cassette.model_responses[:-1]})

    with pytest.raises(ReplayMismatchError, match="called once more than recorded"):
        await replay(short, app_conninfo=db.app_dsn, mcp_conninfo=db.mcp_dsn)
