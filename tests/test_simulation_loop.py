from datetime import timedelta
from pathlib import Path

import psycopg
import pytest

from aml_investigator.core.settings import Settings
from aml_investigator.evaluation.systems import EvalAlert, Recommendation, SystemDecision
from aml_investigator.simulation import MAX_INVESTIGATIONS
from aml_investigator.simulation.loop import (
    DailyLoop,
    SimulationRun,
    WorldDays,
    monitoring_day,
    read_actors,
    simulate,
)
from aml_investigator.simulation.segments import load_segments
from aml_investigator.simulation.world import (
    NotASimulationDatabaseError,
    build_world,
    history_days,
    live_days,
    load_config,
)
from tests.conftest import Database, load_regulations
from tests.simulation_fixtures import prepare_workspace, settings_for


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
def settings(world_db: Database, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Settings:
    settings = settings_for(world_db)
    prepare_workspace(tmp_path, settings.database)
    monkeypatch.chdir(tmp_path)
    load_regulations(world_db)
    return settings


@pytest.mark.parametrize(
    ("cadence", "expected"),
    [(1, list(range(1, 29))), (3, [3, 6, 9, 12, 15, 18, 21, 24, 27, 28]), (7, [7, 14, 21, 28])],
)
def test_monitoring_days_end_on_the_last_day(cadence: int, expected: list[int]) -> None:
    assert [index for index in range(1, 29) if monitoring_day(index, cadence)] == expected


async def b4_run(settings: Settings, cadence: int = 1, seed: int = 31) -> SimulationRun:
    return await simulate(settings, seed=seed, system="b4", cadence=cadence)


class Fixed:
    def __init__(self, name: str, recommendation: Recommendation) -> None:
        self.name = name
        self.recommendation = recommendation

    async def decide(self, alert: EvalAlert) -> SystemDecision:  # noqa: ARG002
        return SystemDecision(self.recommendation)


async def drive(
    settings: Settings, name: str, recommendation: Recommendation, limit: int = MAX_INVESTIGATIONS
) -> DailyLoop:
    with psycopg.connect(settings.owner_conninfo, autocommit=True) as conn:
        days = WorldDays(conn, build_world(31, load_config()))
        days.populate()
        actors = read_actors(settings.evaluator_conninfo)
        loop = DailyLoop(days, actors, load_segments(settings.database), 1, limit)
        await loop.run(Fixed(name, recommendation), Fixed("b4", "close"), None)
    return loop


@pytest.mark.anyio
async def test_the_same_seed_and_system_give_the_same_transaction_hash(settings: Settings) -> None:
    assert (await b4_run(settings)).tape_sha256 == (await b4_run(settings)).tape_sha256


@pytest.mark.anyio
async def test_replay_prefix_then_advance_matches_uninterrupted_timeline(settings: Settings) -> None:
    dates = live_days()[:4]
    split = dates[1]
    segments = load_segments(settings.database)
    with psycopg.connect(settings.owner_conninfo, autocommit=True) as conn:
        uninterrupted = WorldDays(conn, build_world(31, load_config()))
        uninterrupted.populate()
        actors = read_actors(settings.evaluator_conninfo)
        blocked_actor = next(actor for actor in actors.values() if actor.group_id is not None)
        direct = DailyLoop(uninterrupted, actors, segments, 1, MAX_INVESTIGATIONS)
        direct.replay(dates, {split: [blocked_actor]}, write=True)
        direct_ids = [row[0] for row in conn.execute("SELECT txn_id FROM core.transactions ORDER BY txn_id").fetchall()]

        resumed = WorldDays(conn, build_world(31, load_config()))
        resumed.populate()
        prefix = DailyLoop(resumed, actors, segments, 1, MAX_INVESTIGATIONS)
        prefix.replay(dates[:2], {split: [blocked_actor]})
        prefix_end_txn_id = resumed.tape.next_txn_id
        await prefix.advance(dates[2:], Fixed("b4", "close"), Fixed("b4", "close"), None)
        resumed_ids = [
            row[0] for row in conn.execute("SELECT txn_id FROM core.transactions ORDER BY txn_id").fetchall()
        ]

    assert resumed.tape.sha256 == uninterrupted.tape.sha256
    assert resumed.tape.next_txn_id == uninterrupted.tape.next_txn_id
    assert resumed_ids == [txn_id for txn_id in direct_ids if txn_id >= prefix_end_txn_id]
    assert resumed.blocked == uninterrupted.blocked == {blocked_actor.account_id: split + timedelta(days=1)}


@pytest.mark.anyio
async def test_advance_keeps_last_completed_day_and_does_not_continue_after_cap(settings: Settings) -> None:
    with psycopg.connect(settings.owner_conninfo, autocommit=True) as conn:
        days = WorldDays(conn, build_world(31, load_config()))
        days.populate()
        loop = DailyLoop(days, {}, load_segments(settings.database), 1, MAX_INVESTIGATIONS)
        await loop.advance([], Fixed("b4", "close"), Fixed("b4", "close"), None)
        loop.truncated = True
        before = (days.tape.sha256, days.tape.next_txn_id)

        await loop.advance([live_days()[0]], Fixed("b4", "close"), Fixed("b4", "close"), None)

    assert before == (days.tape.sha256, days.tape.next_txn_id)
    assert loop.last_day == history_days()[-1]


@pytest.mark.anyio
async def test_another_seed_gives_another_hash(settings: Settings) -> None:
    assert (await b4_run(settings)).tape_sha256 != (await b4_run(settings, seed=32)).tape_sha256


@pytest.mark.anyio
async def test_only_live_days_produce_alerts_and_externals_never_do(settings: Settings, world_db: Database) -> None:
    run = await b4_run(settings)

    assert run.cases
    assert all(case.alert.alert_date >= live_days()[0] for case in run.cases)
    with world_db.owner() as conn:
        external = conn.execute(
            "SELECT count(*) FROM core.alerts a JOIN core.accounts c USING (account_id) WHERE c.bank_id <> 1"
        ).fetchone()
    assert external == (0,)


@pytest.mark.anyio
async def test_escalated_group_accounts_are_blocked_and_stop_transacting(
    settings: Settings, world_db: Database
) -> None:
    loop = await drive(settings, "b4", "escalate")

    assert loop.days.blocked
    assert any(group.first_block_day is not None for group in loop.days.laundering.progress())
    with world_db.owner() as conn:
        for account, first_blocked_day in loop.days.blocked.items():
            later = conn.execute(
                "SELECT count(*) FROM core.transactions WHERE (from_account = %s OR to_account = %s) AND ts >= %s",
                (account, account, first_blocked_day),
            ).fetchone()
            assert later == (0,), account


@pytest.mark.anyio
async def test_a_block_starts_the_day_after_the_decision(settings: Settings) -> None:
    loop = await drive(settings, "b4", "escalate")
    decided = {item.alert.account_id: item.decided_on for item in reversed(loop.decided)}

    assert loop.days.blocked
    for account, first_blocked_day in loop.days.blocked.items():
        assert first_blocked_day == decided[account] + timedelta(days=1)


@pytest.mark.anyio
async def test_only_group_accounts_are_blocked(settings: Settings) -> None:
    loop = await drive(settings, "b4", "escalate")
    honest_escalated = {
        item.alert.account_id for item in loop.decided if loop.actors[item.alert.account_id].group_id is None
    }

    assert honest_escalated
    assert not honest_escalated & set(loop.days.blocked)


@pytest.mark.anyio
async def test_alerts_of_a_cadence_are_decided_on_monitoring_days(settings: Settings) -> None:
    run = await b4_run(settings, cadence=7)

    assert {case.decided_on for case in run.cases} <= {
        live_days()[6],
        live_days()[13],
        live_days()[20],
        live_days()[27],
    }


@pytest.mark.anyio
async def test_free_systems_have_no_shadow_and_are_not_capped(settings: Settings) -> None:
    run = await b4_run(settings)

    assert all(case.shadow is None for case in run.cases)
    assert not run.truncated
    assert run.last_day == live_days()[-1]


@pytest.mark.anyio
async def test_b1_turns_scores_into_recommendations_at_the_stored_operating_point(settings: Settings) -> None:
    run = await simulate(settings, seed=31, system="b1", cadence=1)

    assert run.cases
    assert all(case.decision.risk_score is not None for case in run.cases)
    assert {case.recommendation for case in run.cases} <= {"close", "escalate"}


@pytest.mark.anyio
async def test_an_agent_stops_at_the_cap_and_marks_the_run_truncated(settings: Settings) -> None:
    loop = await drive(settings, "scripted", "close", limit=2)

    assert len(loop.decided) == 2
    assert loop.truncated
    assert loop.undecided_alerts >= 1
    assert loop.last_day < live_days()[-1]
    assert {item.shadow for item in loop.decided} == {"close"}


@pytest.mark.anyio
async def test_the_cap_cannot_be_raised(settings: Settings) -> None:
    with pytest.raises(ValueError, match="60"):
        await simulate(settings, seed=31, system="scripted", cadence=1, limit=MAX_INVESTIGATIONS + 1)


@pytest.mark.anyio
async def test_any_other_database_is_refused(db: Database) -> None:
    with pytest.raises(NotASimulationDatabaseError):
        await simulate(settings_for(db), seed=31, system="b4", cadence=1)
