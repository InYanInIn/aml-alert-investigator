from collections.abc import Iterator
from pathlib import Path
from typing import Any

import psycopg
import pytest
from fastapi.testclient import TestClient

from aml_investigator.core.settings import Settings
from aml_investigator.mcp_server.server import build_server
from aml_investigator.monitoring.alerts import build_alerts
from aml_investigator.monitoring.rules import r2_fan_out
from aml_investigator.web.app import create_app
from tests.conftest import Database
from tests.test_ingest import run_ingest, write_raw

SCRIPT = "<script>alert(1)</script>"


@pytest.fixture
def seeded(db: Database, tmp_path: Path) -> Database:
    run_ingest(db, write_raw(tmp_path))
    with psycopg.connect(db.owner_dsn) as conn:
        assert r2_fan_out.generate(conn) > 0
        assert build_alerts(conn, {"R2_fan_out": 1}, suppression_days=0) > 0
    return db


@pytest.fixture
def alert_id(seeded: Database) -> int:
    return int(str(rows(seeded, "SELECT min(alert_id) FROM core.alerts")[0][0]))


@pytest.fixture
def client(seeded: Database) -> Iterator[TestClient]:
    settings = Settings(seeded.owner_dsn, seeded.mcp_dsn, seeded.app_dsn, seeded.evaluator_dsn)
    with TestClient(create_app(settings, mcp=build_server(seeded.mcp_dsn)), follow_redirects=False) as client:
        yield client


def rows(db: Database, query: str) -> list[tuple[Any, ...]]:
    with db.owner() as conn:
        return conn.execute(query).fetchall()


def investigate(client: TestClient, db: Database, alert_id: int) -> int:
    response = client.post(f"/alerts/{alert_id}/investigate")
    assert response.status_code == 303
    assert response.headers["location"] == f"/alerts/{alert_id}"
    [(investigation_id,)] = rows(db, "SELECT investigation_id FROM review.investigations")
    return int(investigation_id)


def test_queue_lists_the_alert(client: TestClient, alert_id: int) -> None:
    page = client.get("/").text

    assert f'href="/alerts/{alert_id}"' in page
    assert "R2_fan_out" in page
    assert "<td>none</td>" in page


def test_investigate_confirm_and_idempotent_decision(client: TestClient, seeded: Database, alert_id: int) -> None:
    investigation_id = investigate(client, seeded, alert_id)

    [(verification, decision)] = rows(seeded, "SELECT verification, decision FROM review.investigations")
    cited = decision["what"][0]["txn_ids"]
    assert verification["status"] == "passed"
    assert cited

    page = client.get(f"/alerts/{alert_id}").text
    assert '<span class="badge escalate">escalate</span>' in page
    assert '<span class="badge passed">passed</span>' in page
    assert f'href="#txn-{cited[0]}"' in page
    assert f'id="txn-{cited[0]}"' in page
    assert f'action="/investigations/{investigation_id}/decision"' in page
    assert 'name="final_decision"' in page
    assert "awaiting review" in client.get("/").text

    for _ in range(2):
        response = client.post(
            f"/investigations/{investigation_id}/decision", data={"final_decision": "close", "comment": "payroll"}
        )
        assert response.status_code == 303
        assert response.headers["location"] == f"/alerts/{alert_id}"

    assert rows(seeded, "SELECT investigation_id, analyst, final_decision, comment FROM review.decisions") == [
        (investigation_id, "analyst", "close", "payroll")
    ]
    assert "decided: close" in client.get("/").text
    assert 'name="final_decision"' not in client.get(f"/alerts/{alert_id}").text


def test_unknown_alert_and_investigation_are_404(client: TestClient, alert_id: int) -> None:
    unknown = alert_id + 1000

    assert client.get(f"/alerts/{unknown}").status_code == 404
    assert client.post(f"/alerts/{unknown}/investigate").status_code == 404
    assert client.post("/investigations/999999/decision", data={"final_decision": "close"}).status_code == 404


def test_untrusted_text_is_escaped(client: TestClient, seeded: Database, alert_id: int) -> None:
    investigation_id = investigate(client, seeded, alert_id)
    client.post(f"/investigations/{investigation_id}/decision", data={"final_decision": "close", "comment": SCRIPT})

    page = client.get(f"/alerts/{alert_id}").text

    assert SCRIPT not in page
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in page
