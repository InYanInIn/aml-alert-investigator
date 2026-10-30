import uuid
from collections.abc import Iterator
from dataclasses import dataclass

import psycopg
import pytest
from psycopg import sql

from aml_investigator.core.migrate import migrate
from aml_investigator.core.settings import Settings, load_settings


@dataclass(frozen=True)
class Database:
    owner_dsn: str
    mcp_dsn: str
    app_dsn: str
    evaluator_dsn: str

    def owner(self) -> psycopg.Connection[tuple[object, ...]]:
        return psycopg.connect(self.owner_dsn, autocommit=True)


def retarget(settings: Settings, dbname: str) -> Database:
    test = settings.with_database(dbname)
    return Database(test.owner_dsn, test.mcp_dsn, test.app_dsn, test.evaluator_dsn)


@pytest.fixture(scope="session")
def database() -> Iterator[Database]:
    settings = load_settings()
    dbname = f"aml_test_{uuid.uuid4().hex[:8]}"
    with psycopg.connect(settings.owner_dsn, autocommit=True) as admin:
        admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(dbname)))
    test_db = retarget(settings, dbname)
    try:
        migrate(test_db.owner_dsn, (test_db.mcp_dsn, test_db.app_dsn, test_db.evaluator_dsn))
        yield test_db
    finally:
        with psycopg.connect(settings.owner_dsn, autocommit=True) as admin:
            admin.execute(sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(sql.Identifier(dbname)))


@pytest.fixture
def db(database: Database) -> Database:
    with database.owner() as conn:
        conn.execute(
            "TRUNCATE review.decisions, review.investigations, truth.alert_truth, truth.pattern_attempts, "
            "truth.transaction_labels, core.alert_rule_hits, core.alerts, core.rule_candidates, core.transactions, "
            "core.fx_rates, core.accounts RESTART IDENTITY CASCADE"
        )
    return database
