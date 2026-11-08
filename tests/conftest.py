import uuid
from collections.abc import Iterator
from dataclasses import dataclass

import psycopg
import pytest
from psycopg import sql

from aml_investigator.core.migrate import migrate
from aml_investigator.core.settings import Settings, load_settings
from aml_investigator.simulation.world import reset_world


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
    return Database(test.owner_conninfo, test.mcp_conninfo, test.app_conninfo, test.evaluator_conninfo)


def created_database(prefix: str) -> Iterator[Database]:
    settings = load_settings()
    dbname = f"{prefix}_{uuid.uuid4().hex[:8]}"
    with psycopg.connect(settings.owner_conninfo, autocommit=True) as admin:
        admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(dbname)))
    test_db = retarget(settings, dbname)
    try:
        migrate(test_db.owner_dsn, (test_db.mcp_dsn, test_db.app_dsn, test_db.evaluator_dsn))
        yield test_db
    finally:
        with psycopg.connect(settings.owner_conninfo, autocommit=True) as admin:
            admin.execute(sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(sql.Identifier(dbname)))


@pytest.fixture(scope="session")
def database() -> Iterator[Database]:
    yield from created_database("aml_test")


@pytest.fixture(scope="session")
def simulation_database() -> Iterator[Database]:
    yield from created_database("aml_sim_test")


@pytest.fixture
def world_db(simulation_database: Database) -> Database:
    with simulation_database.owner() as conn:
        reset_world(conn)
    return simulation_database


@pytest.fixture
def db(database: Database) -> Database:
    with database.owner() as conn:
        conn.execute(
            "TRUNCATE review.qa_reviews, review.decisions, review.investigations, truth.alert_truth, "
            "truth.candidate_truth, truth.pattern_attempts, truth.transaction_labels, core.alert_rule_hits, "
            "core.alerts, core.rule_candidates, core.transactions, core.fx_rates, core.customer_profiles, "
            "core.accounts "
            "RESTART IDENTITY CASCADE"
        )
    return database


REGULATION_PASSAGES = (
    (
        "AMLR art. 69(1)",
        "amlr",
        "Obliged entities shall report to the FIU where they have reasonable grounds to suspect criminal activity.",
        "english",
    ),
    (
        "AMLR art. 34(2)",
        "amlr",
        "Obliged entities shall examine the purpose of a transaction that is complex or unusually large.",
        "english",
    ),
    (
        "PL-AML art. 74 ust. 1",
        "pl-aml",
        "Instytucja obowiązana zawiadamia Generalnego Inspektora o okolicznościach wskazujących na podejrzenie.",
        "simple",
    ),
)


@pytest.fixture
def regulations(db: Database) -> list[str]:
    return load_regulations(db)


def load_regulations(db: Database) -> list[str]:
    with db.owner() as conn:
        conn.execute("DELETE FROM corpus.passages")
        conn.execute("DELETE FROM corpus.documents")
        conn.execute(
            "INSERT INTO corpus.documents VALUES ('amlr', 'AMLR', 'en', '2024', 'https://example.test/amlr', 'x'), "
            "('pl-aml', 'Ustawa', 'pl', '2025', 'https://example.test/pl', 'y')"
        )
        for position, (passage_id, doc_id, text, config) in enumerate(REGULATION_PASSAGES):
            conn.execute(
                "INSERT INTO corpus.passages (passage_id, doc_id, article, paragraph, position, heading, text, "
                "content_sha256, search_config) "
                "VALUES (%s, %s, '1', '1', %s, NULL, %s, encode(sha256(convert_to(%s, 'UTF8')), 'hex'), %s)",
                (passage_id, doc_id, position, text, text, config),
            )
    return [passage_id for passage_id, *_ in REGULATION_PASSAGES]
