import time
from collections.abc import Iterator
from typing import cast

import psycopg
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import SecretStr

from aml_investigator.core.settings import Settings
from aml_investigator.investigation.models.rule_score import RuleScoreChatModel
from aml_investigator.mcp_server.server import build_server
from aml_investigator.simulation.periods import RESET_WORLD
from aml_investigator.web.app import create_app
from tests.conftest import Database

COUNTS = {"student": 2, "worker": 2, "office_employee": 2, "business_owner": 2, "corporation": 2}
DEFAULT_COUNTS = {"student": 200, "worker": 200, "office_employee": 200, "business_owner": 150, "corporation": 80}


@pytest.fixture
def ui_client(simulation_database: Database) -> Iterator[tuple[TestClient, Database]]:
    with psycopg.connect(simulation_database.owner_dsn, autocommit=True) as conn:
        conn.execute(RESET_WORLD)
    settings = Settings(
        owner_dsn=SecretStr(simulation_database.owner_dsn),
        mcp_dsn=SecretStr(simulation_database.mcp_dsn),
        app_dsn=SecretStr(simulation_database.app_dsn),
        evaluator_dsn=SecretStr(simulation_database.evaluator_dsn),
    )
    app = create_app(
        settings,
        mcp=build_server(simulation_database.mcp_dsn, customer_profile=True),
        model=RuleScoreChatModel(thresholds={}, close_below=0),
        ui_mode=True,
    )
    with TestClient(app) as client:
        yield client, simulation_database


def test_world_reads_hide_seed_and_evaluator(ui_client: tuple[TestClient, Database]) -> None:
    client, db = ui_client
    assert client.get("/api/world").json()["day"] == 0
    assert client.get("/api/evaluator/groups").status_code == 404
    assert client.post("/api/world", json={"seed": 2026, "persona_counts": COUNTS}).status_code == 202

    state = client.get("/api/world").json()
    assert state["day"] == 21
    assert state["first_live_day"] == 22
    assert state["thresholds_banner"] == "thresholds calibrated for 850 accounts; alert volume will differ"
    assert "seed" not in str(state)
    assert "generation" not in str(state)
    assert "scenario" not in str(state)

    with psycopg.connect(db.app_dsn) as conn, pytest.raises(psycopg.errors.InsufficientPrivilege):
        conn.execute("SELECT * FROM truth.sim_actors").fetchall()


def test_threshold_banner_uses_exact_default_population(ui_client: tuple[TestClient, Database]) -> None:
    client, db = ui_client
    assert client.post("/api/world", json={"seed": 2026, "persona_counts": COUNTS}).status_code == 202
    with psycopg.connect(db.owner_dsn) as conn:
        conn.execute("UPDATE sim.world SET persona_counts = %s", (psycopg.types.json.Jsonb(DEFAULT_COUNTS),))
    assert client.get("/api/world").json()["thresholds_banner"] is None
    changed = {**DEFAULT_COUNTS, "student": 201, "worker": 199}
    with psycopg.connect(db.owner_dsn) as conn:
        conn.execute("UPDATE sim.world SET persona_counts = %s", (psycopg.types.json.Jsonb(changed),))
    assert client.get("/api/world").json()["thresholds_banner"] is not None


def test_evaluator_groups_require_separate_flag(ui_client: tuple[TestClient, Database]) -> None:
    client, db = ui_client
    assert client.post("/api/world", json={"seed": 19, "persona_counts": COUNTS}).status_code == 202
    assert client.get("/api/evaluator/groups").status_code == 404
    enabled = create_app(
        cast(FastAPI, client.app).state.simulation_jobs.settings,
        mcp=build_server(db.mcp_dsn, customer_profile=True),
        model=RuleScoreChatModel(thresholds={}, close_below=0),
        ui_mode=True,
        evaluator=True,
    )
    with TestClient(enabled) as evaluator_client:
        groups = evaluator_client.get("/api/evaluator/groups").json()
    assert groups
    assert set(groups[0]) == {"account_id", "group_id", "scenario", "first_payment_day", "blocked_on"}


def test_graph_clients_and_read_only_analyst(ui_client: tuple[TestClient, Database]) -> None:
    client, _ = ui_client
    assert client.post("/api/world", json={"seed": 19, "persona_counts": COUNTS}).status_code == 202
    graph = client.get("/api/world/graph?day=21").json()
    assert graph["arcs"]
    assert any(node["id"].startswith("external:") for node in graph["clients"])
    assert all("group" not in node and "scenario" not in node for node in graph["clients"])
    assert client.get("/api/world/timeline").json()
    clients = client.get("/api/clients").json()
    assert clients
    detail = client.get(f"/api/clients/{clients[0]['id']}").json()
    assert isinstance(detail["balance"], float)
    assert detail["payments"]
    assert "reference" in detail["payments"][0]
    assert client.get("/").status_code == 200
    assert client.get("/alerts").status_code == 200
    assert client.post("/alerts/1/investigate").status_code == 404
    assert client.post("/investigations/1/decision").status_code == 404


def test_b4_period_filter_and_no_prepared_case(ui_client: tuple[TestClient, Database]) -> None:
    client, db = ui_client
    assert client.post("/api/world", json={"seed": 19, "persona_counts": COUNTS}).status_code == 202
    started = client.post("/api/simulate", json={"days": 7, "system": "b4"})
    assert started.status_code == 202
    for _ in range(400):
        current = client.get("/api/jobs/current").json()
        if current["status"] != "running":
            break
        time.sleep(0.05)
    assert current["status"] == "done"
    period_id = started.json()["period_id"]
    state = client.get("/api/world").json()
    assert state["day"] == 28
    with psycopg.connect(db.owner_dsn) as conn:
        accounts = [
            row[0]
            for row in conn.execute(
                "SELECT account_id FROM core.accounts WHERE bank_id = 1 ORDER BY account_id LIMIT 2"
            )
        ]
        alert_ids = []
        for account, day in zip(accounts, ("2025-03-29", "2025-03-30"), strict=True):
            [(alert_id,)] = conn.execute(
                "INSERT INTO core.alerts (account_id, alert_date, window_start, window_end) "
                "VALUES (%s, %s, %s::date, %s::date) RETURNING alert_id",
                (account, day, day, day),
            ).fetchall()
            conn.execute("INSERT INTO sim.alert_period VALUES (%s, %s)", (alert_id, period_id))
            alert_ids.append(alert_id)
        conn.execute("INSERT INTO sim.decision (alert_id, recommendation) VALUES (%s, 'close')", (alert_ids[0],))
        conn.execute(
            "INSERT INTO sim.decision (alert_id, skip_reason) VALUES (%s, 'investigation_cap')",
            (alert_ids[1],),
        )
    page = client.get(f"/alerts?period={period_id}").text
    assert "Simulation period" in page
    assert "Day 22\u201328 (1 week, b4)" in page
    assert "Day 22\u201328 (1 week, b4)" in client.get(f"/?period={period_id}").text
    assert "rule B4, no prepared case" in page
    assert "not investigated" in page
    assert "rule B4, no prepared case" in client.get(f"/alerts/{alert_ids[0]}").text
    assert "not investigated" in client.get(f"/alerts/{alert_ids[1]}").text
    assert client.get("/alerts?period=99999").text.count("<td>") == 0
