import asyncio
from dataclasses import replace
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Literal

import psycopg
import pytest

from aml_investigator.core.db import FixedRole, ScopedDatabaseError
from aml_investigator.core.logging import JsonFormatter
from aml_investigator.core.settings import Settings
from aml_investigator.evaluation.baselines.agent import open_agent
from aml_investigator.evaluation.baselines.runtime import RuntimeSettings
from aml_investigator.evaluation.systems import EvalAlert
from aml_investigator.investigation.decision import Decision
from aml_investigator.investigation.models.rule_score import RuleScoreChatModel
from aml_investigator.simulation.world import history_days, live_days
from aml_investigator.web.jobs import JobConflictError, JobStatus, SimulationJobs
from tests.conftest import Database, load_regulations
from tests.simulation_fixtures import THRESHOLDS, prepare_workspace, settings_for


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
def ui_settings(world_db: Database, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Settings:
    prepare_workspace(tmp_path, "aml_sim_dev")
    monkeypatch.chdir(tmp_path)
    load_regulations(world_db)
    return settings_for(world_db)


async def finished(jobs: SimulationJobs, period_id: int) -> JobStatus:
    async with asyncio.timeout(60):
        while True:
            status = jobs.current()
            if status is not None and status.period_id == period_id and status.status != "running":
                return status
            await asyncio.sleep(0.05)


@pytest.mark.anyio
async def test_b4_jobs_publish_history_and_resume_without_duplicate_days(
    ui_settings: Settings, world_db: Database
) -> None:
    jobs = SimulationJobs(ui_settings)
    await jobs.create(51, {})
    history = await finished(jobs, 0)
    assert (history.day, history.days_total, history.investigations) == (history_days()[-1], 21, 0)

    first = await jobs.start(7, "b4")
    with pytest.raises(JobConflictError, match="already running"):
        await jobs.start(1, "b4")
    completed = await finished(jobs, first)
    assert (completed.status, completed.day, completed.days_total, completed.cost_usd) == (
        "done",
        live_days()[6],
        7,
        0,
    )
    with world_db.owner() as conn:
        [(decisions,)] = conn.execute("SELECT count(*) FROM sim.decision").fetchall()
        assert int(str(decisions)) > 0
        assert conn.execute("SELECT count(*) FROM review.investigations").fetchone() == (0,)
        prefix = conn.execute("SELECT txn_id, ts FROM core.transactions ORDER BY txn_id").fetchall()

    restarted = SimulationJobs(ui_settings)
    restarted.recover()
    second = await finished(restarted, await restarted.start(1, "b4"))
    assert second.status == "done"
    assert second.day == completed.day + timedelta(days=1)
    with world_db.owner() as conn:
        all_rows = conn.execute("SELECT txn_id, ts FROM core.transactions ORDER BY txn_id").fetchall()
        assert all_rows[: len(prefix)] == prefix
        assert len(all_rows) > len(prefix)
        assert [row[0] for row in all_rows] == list(range(1, len(all_rows) + 1))


@pytest.mark.anyio
async def test_prepared_day_rolls_back_before_restart(
    ui_settings: Settings, world_db: Database, caplog: pytest.LogCaptureFixture
) -> None:
    jobs = SimulationJobs(ui_settings)
    await jobs.create(51, {})
    with world_db.owner() as conn:
        tape = conn.execute("SELECT txn_id, ts FROM core.transactions ORDER BY txn_id").fetchall()
        conn.execute(
            "CREATE FUNCTION sim.fail_publish() RETURNS trigger LANGUAGE plpgsql AS "
            "$$ BEGIN RAISE EXCEPTION 'private crash details'; END $$"
        )
        conn.execute(
            "CREATE TRIGGER fail_publish AFTER INSERT ON sim.alert_period FOR EACH STATEMENT "
            "EXECUTE FUNCTION sim.fail_publish()"
        )
    try:
        period = await jobs.start(1, "b4")
        failed = await finished(jobs, period)
        assert failed.status == "failed"
        assert failed.day == history_days()[-1]
        assert failed.error == "Simulation failed. The last completed day is available."
        [record] = [record for record in caplog.records if record.message == "simulation_period_failed"]
        assert vars(record)["fields"] == {
            "period_id": period,
            "day": live_days()[0],
            "error_class": "RaiseException",
        }
        with world_db.owner() as conn:
            assert conn.execute("SELECT txn_id, ts FROM core.transactions ORDER BY txn_id").fetchall() == tape
            assert conn.execute("SELECT count(*) FROM core.alerts").fetchone() == (0,)
            assert conn.execute("SELECT count(*) FROM sim.block").fetchone() == (0,)
    finally:
        with world_db.owner() as conn:
            conn.execute("DROP TRIGGER fail_publish ON sim.alert_period")
            conn.execute("DROP FUNCTION sim.fail_publish()")

    restart = SimulationJobs(ui_settings)
    restart.recover()
    resumed = await finished(restart, await restart.start(1, "b4"))
    assert (resumed.status, resumed.day) == ("done", live_days()[0])


@pytest.mark.anyio
async def test_restart_marks_stale_period_failed_and_refuses_a_changed_hash(
    ui_settings: Settings, world_db: Database
) -> None:
    jobs = SimulationJobs(ui_settings)
    await jobs.create(51, {})
    with world_db.owner() as conn:
        conn.execute(
            "INSERT INTO sim.period (first_day, last_day, target_day, system, investigation_cap, status, tape_sha256) "
            "VALUES (%s, %s, %s, 'b4', 0, 'running', 'changed')",
            (live_days()[0], history_days()[-1], live_days()[0]),
        )

    jobs.recover()
    stale = jobs.current()
    assert stale is not None
    assert (stale.status, stale.day) == ("failed", history_days()[-1])
    failed = await finished(jobs, await jobs.start(1, "b4"))
    assert (failed.status, failed.day, failed.error) == ("failed", history_days()[-1], "World no longer reproducible.")
    with world_db.owner() as conn:
        assert conn.execute("SELECT count(*) FROM core.transactions WHERE ts >= %s", (live_days()[0],)).fetchone() == (
            0,
        )


class ProfileModel(RuleScoreChatModel):
    def draft(self, results: list[tuple[str, dict[str, Any]]]) -> Decision:
        assert any(
            tool == "get_account_profile" and content.get("customer_profile_untrusted") for tool, content in results
        )
        return super().draft(results)


@pytest.mark.anyio
async def test_profile_case_rolls_back_and_matches_m9_after_resume(
    ui_settings: Settings, world_db: Database, caplog: pytest.LogCaptureFixture
) -> None:
    model = ProfileModel(thresholds=THRESHOLDS, close_below=1.5)
    jobs = SimulationJobs(ui_settings, profile_model=model)
    await jobs.create(51, {})
    with world_db.owner() as conn:
        conn.execute(
            "CREATE FUNCTION sim.fail_case() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN "
            "IF EXISTS (SELECT 1 FROM review.investigations WHERE alert_id = NEW.alert_id) THEN "
            "RAISE EXCEPTION 'case was persisted'; END IF; RETURN NEW; END $$"
        )
        conn.execute(
            "CREATE TRIGGER fail_case BEFORE INSERT ON sim.decision FOR EACH ROW EXECUTE FUNCTION sim.fail_case()"
        )
    try:
        failed = await finished(jobs, await jobs.start(7, "azure_profile"))
        assert failed.status == "failed"
        assert failed.investigations == 0
        [record] = [record for record in caplog.records if record.message == "simulation_period_failed"]
        assert vars(record)["fields"]["error_class"] == "RaiseException"
        with world_db.owner() as conn:
            assert conn.execute("SELECT count(*) FROM review.investigations").fetchone() == (0,)
            assert conn.execute(
                "SELECT count(*) FROM core.transactions WHERE ts >= %s", (failed.day + timedelta(days=1),)
            ).fetchone() == (0,)
    finally:
        with world_db.owner() as conn:
            conn.execute("DROP TRIGGER fail_case ON sim.decision")
            conn.execute("DROP FUNCTION sim.fail_case()")

    restarted = SimulationJobs(ui_settings, profile_model=model)
    resumed = await finished(restarted, await restarted.start(7, "azure_profile"))
    assert resumed.status == "done"
    assert resumed.investigations > 0
    with world_db.owner() as conn:
        [[alert_id, account, day, prompt, decision, verification]] = conn.execute(
            "SELECT alert_id, account_id, alert_date, prompt_version, decision, verification "
            "FROM review.investigations JOIN core.alerts USING (alert_id) ORDER BY investigation_id LIMIT 1"
        ).fetchall()
    runtime = replace(RuntimeSettings.of(ui_settings), customer_profile=True)
    assert isinstance(day, date)
    async with open_agent(runtime, model, "azure_profile") as agent:
        await agent.decide(EvalAlert(int(str(alert_id)), str(account), day))
    with world_db.owner() as conn:
        actual = conn.execute(
            "SELECT prompt_version, decision, verification FROM review.investigations "
            "WHERE alert_id = %s ORDER BY investigation_id DESC LIMIT 1",
            (alert_id,),
        ).fetchone()
    assert actual == (prompt, decision, verification)


@pytest.mark.anyio
async def test_mcp_database_error_rolls_back_even_when_the_tool_error_is_recoverable(
    ui_settings: Settings, world_db: Database, caplog: pytest.LogCaptureFixture
) -> None:
    jobs = SimulationJobs(ui_settings, profile_model=RuleScoreChatModel(thresholds=THRESHOLDS, close_below=1.5))
    await jobs.create(51, {})
    with world_db.owner() as conn:
        conn.execute(
            "CREATE FUNCTION core.reject_profile_read() RETURNS boolean LANGUAGE plpgsql AS "
            "$$ BEGIN RAISE EXCEPTION 'private profile database detail'; END $$"
        )
        conn.execute("ALTER TABLE core.customer_profiles ENABLE ROW LEVEL SECURITY")
        conn.execute(
            "CREATE POLICY reject_profile_read ON core.customer_profiles FOR SELECT TO aml_mcp_reader "
            "USING (core.reject_profile_read())"
        )
    try:
        result = await finished(jobs, await jobs.start(7, "azure_profile"))
        assert result.status == "failed"
        assert result.investigations == 0
        assert result.error == "Simulation failed. The last completed day is available."
        [record] = [record for record in caplog.records if record.message == "simulation_period_failed"]
        assert vars(record)["fields"]["error_class"] == "RaiseException"
        assert all("private profile database detail" not in JsonFormatter().format(item) for item in caplog.records)
        with world_db.owner() as conn:
            assert conn.execute("SELECT count(*) FROM review.investigations").fetchone() == (0,)
            assert conn.execute(
                "SELECT count(*) FROM core.transactions WHERE ts >= %s", (result.day + timedelta(days=1),)
            ).fetchone() == (0,)
    finally:
        with world_db.owner() as conn:
            conn.execute("DROP POLICY reject_profile_read ON core.customer_profiles")
            conn.execute("ALTER TABLE core.customer_profiles DISABLE ROW LEVEL SECURITY")
            conn.execute("DROP FUNCTION core.reject_profile_read()")


@pytest.mark.parametrize(
    ("role", "statement"),
    [
        ("app_dsn", "SELECT seed, generation, segment_thresholds FROM sim.world"),
        ("app_dsn", "SELECT reserved_usd FROM sim.azure_spend"),
        ("app_dsn", "UPDATE sim.period SET status = 'done'"),
        ("mcp_dsn", "SELECT persona_counts FROM sim.world"),
        ("evaluator_dsn", "SELECT seed FROM sim.world"),
    ],
)
def test_ui_roles_cannot_read_private_world_data_or_mutate_progress(
    world_db: Database, role: str, statement: str
) -> None:
    with psycopg.connect(getattr(world_db, role)) as conn, pytest.raises(psycopg.errors.InsufficientPrivilege):
        conn.execute(statement)


@pytest.mark.anyio
@pytest.mark.parametrize(("role", "expected"), [("app", "aml_app"), ("mcp", "aml_mcp_reader")])
async def test_day_role_restores_the_owner_after_a_forbidden_query(
    world_db: Database, role: Literal["app", "mcp"], expected: str
) -> None:
    with psycopg.connect(world_db.owner_dsn, autocommit=True) as owner, owner.transaction():
        fixed = FixedRole(owner, asyncio.Lock(), role)

        async def forbidden() -> None:
            async with fixed.connection() as conn:
                await conn.execute("SELECT persona FROM truth.sim_actors")

        async with fixed.connection() as conn:
            assert await (await conn.execute("SELECT current_user AS username")).fetchone() == {"username": expected}
        with pytest.raises(ScopedDatabaseError) as failure:
            await forbidden()
        assert failure.value.cause == "InsufficientPrivilege"
        assert owner.execute("SELECT count(*) FROM truth.sim_actors").fetchone() == (0,)
