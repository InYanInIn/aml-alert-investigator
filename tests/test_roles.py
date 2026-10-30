import psycopg
import pytest

from aml_investigator.core.migrate import migrate
from tests.conftest import Database

TRUTH_TABLES = ("truth.transaction_labels", "truth.pattern_attempts")


@pytest.mark.parametrize("table", TRUTH_TABLES)
@pytest.mark.parametrize("role", ["mcp", "app"])
def test_runtime_roles_cannot_read_truth(db: Database, role: str, table: str) -> None:
    dsn = db.mcp_dsn if role == "mcp" else db.app_dsn
    with psycopg.connect(dsn) as conn, pytest.raises(psycopg.errors.InsufficientPrivilege):
        conn.execute(f"SELECT 1 FROM {table} LIMIT 1")  # noqa: S608


@pytest.mark.parametrize("table", TRUTH_TABLES)
def test_evaluator_can_read_truth(db: Database, table: str) -> None:
    with psycopg.connect(db.evaluator_dsn) as conn:
        conn.execute(f"SELECT 1 FROM {table} LIMIT 1")  # noqa: S608


def test_mcp_reader_cannot_write_core(db: Database) -> None:
    with psycopg.connect(db.mcp_dsn) as conn, pytest.raises(psycopg.errors.InsufficientPrivilege):
        conn.execute("INSERT INTO core.accounts VALUES ('A1', 1, 'Bank', 'E1', 'Corporation')")


def test_review_records_are_append_only(db: Database) -> None:
    with db.owner() as conn:
        conn.execute("INSERT INTO core.accounts VALUES ('A1', 1, 'Bank', 'E1', 'Corporation')")
        conn.execute(
            "INSERT INTO core.alerts (account_id, alert_date, window_start, window_end) "
            "VALUES ('A1', '2022-09-02', '2022-09-01', '2022-09-03')"
        )
    with psycopg.connect(db.app_dsn) as conn:
        conn.execute(
            "INSERT INTO review.investigations (alert_id, thread_id, model, prompt_version, recommendation, "
            "decision, verification) VALUES (1, 't1', 'scripted', 'v0', 'close', '{}', '{}')"
        )
        conn.commit()
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            conn.execute("UPDATE review.investigations SET recommendation = 'escalate'")
    with db.owner() as conn, pytest.raises(psycopg.errors.RaiseException):
        conn.execute("UPDATE review.investigations SET recommendation = 'escalate'")


def test_migrate_is_idempotent(database: Database) -> None:
    assert migrate(database.owner_dsn, (database.mcp_dsn, database.app_dsn, database.evaluator_dsn)) == []
