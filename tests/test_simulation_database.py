import psycopg
import pytest

from aml_investigator.simulation.world import (
    NotASimulationDatabaseError,
    copy_corpus,
    require_simulation_database,
    reset_world,
)
from tests.conftest import Database


@pytest.mark.parametrize("name", ["aml_sim_dev", "aml_sim_heldout", "aml_sim_test_ab12cd34"])
def test_simulation_names_are_accepted(name: str) -> None:
    require_simulation_database(name)


@pytest.mark.parametrize("name", ["aml_hi_small", "aml_li_small", "aml", "aml_sim_", "xaml_sim_dev", "aml_sim_dev; x"])
def test_other_names_are_refused(name: str) -> None:
    with pytest.raises(NotASimulationDatabaseError, match="aml_sim_"):
        require_simulation_database(name)


def fill_world(db: Database) -> None:
    with db.owner() as conn:
        conn.execute("INSERT INTO core.accounts VALUES ('A1', 1, 'Bank 1', 'E1', 'Individual')")
        conn.execute("INSERT INTO core.fx_rates VALUES ('Euro', 1)")
        conn.execute("INSERT INTO core.customer_profiles (entity_id, occupation) VALUES ('E1', 'Student')")
        conn.execute("INSERT INTO truth.sim_actors VALUES ('A1', 'student', NULL, NULL)")
        conn.execute(
            "INSERT INTO core.alerts (account_id, alert_date, window_start, window_end) "
            "VALUES ('A1', '2025-03-24', '2025-03-23', '2025-03-25')"
        )
        conn.execute(
            "INSERT INTO review.investigations (alert_id, thread_id, model, prompt_version, recommendation, "
            "decision, verification, route, trace) "
            "VALUES (1, 't1', 'scripted', 'v0', 'close', '{}', '{}', 'qa_sample', '{}')"
        )


def count(db: Database, table: str) -> int:
    with db.owner() as conn:
        row = conn.execute(f"SELECT count(*) FROM {table}").fetchone()  # noqa: S608
    assert row is not None
    return int(str(row[0]))


def test_reset_empties_the_world_despite_the_append_only_trigger(world_db: Database) -> None:
    fill_world(world_db)

    with world_db.owner() as conn:
        reset_world(conn)

    for table in (
        "core.accounts",
        "core.fx_rates",
        "core.customer_profiles",
        "core.alerts",
        "truth.sim_actors",
        "review.investigations",
    ):
        assert count(world_db, table) == 0


def test_reset_restarts_alert_ids(world_db: Database) -> None:
    fill_world(world_db)

    with world_db.owner() as conn:
        reset_world(conn)
    fill_world(world_db)

    with world_db.owner() as conn:
        assert conn.execute("SELECT alert_id FROM core.alerts").fetchall() == [(1,)]


def test_reset_refuses_a_database_that_is_not_a_world(db: Database) -> None:
    with db.owner() as conn, pytest.raises(NotASimulationDatabaseError):
        reset_world(conn)


@pytest.mark.usefixtures("regulations")
def test_copy_corpus_is_repeatable_and_survives_reset(db: Database, world_db: Database) -> None:
    with db.owner() as source, world_db.owner() as target:
        assert copy_corpus(source, target) == 3
        assert copy_corpus(source, target) == 3
        reset_world(target)

    assert count(world_db, "corpus.passages") == 3
    with psycopg.connect(world_db.mcp_dsn) as conn:
        assert conn.execute("SELECT count(*) FROM corpus.passages WHERE search IS NOT NULL").fetchone() == (3,)


def test_copy_corpus_refuses_a_target_that_is_not_a_world(db: Database, world_db: Database) -> None:
    with world_db.owner() as source, db.owner() as target, pytest.raises(NotASimulationDatabaseError):
        copy_corpus(source, target)


def test_mcp_reader_can_read_customer_profiles(db: Database) -> None:
    with psycopg.connect(db.mcp_dsn) as conn:
        conn.execute("SELECT 1 FROM core.customer_profiles LIMIT 1")
