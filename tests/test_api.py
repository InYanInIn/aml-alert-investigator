from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import psycopg
import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from aml_investigator.core.settings import Settings
from aml_investigator.investigation import routing
from aml_investigator.investigation.rule_score_model import RuleScoreChatModel
from aml_investigator.mcp_server.server import build_server
from aml_investigator.monitoring.alerts import build_alerts
from aml_investigator.monitoring.rules import r2_fan_out
from aml_investigator.web.app import create_app
from tests.conftest import Database
from tests.test_ingest import run_ingest, write_raw

SCRIPT = "<script>alert(1)</script>"
ESCALATING = RuleScoreChatModel(thresholds={"R2_fan_out": 1.0}, close_below=0.0)
CLOSING = RuleScoreChatModel(thresholds={"R2_fan_out": 1.0}, close_below=1e9)


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
def alert_ids(seeded: Database, alert_id: int) -> list[int]:
    with seeded.owner() as conn:
        for day in (1, 2):
            conn.execute(
                "WITH copy AS (INSERT INTO core.alerts (account_id, alert_date, window_start, window_end) "
                "SELECT account_id, alert_date + %s, window_start, window_end FROM core.alerts WHERE alert_id = %s "
                "RETURNING alert_id) "
                "INSERT INTO core.alert_rule_hits SELECT copy.alert_id, rule_id, score, evidence_txn_ids "
                "FROM copy, core.alert_rule_hits WHERE alert_rule_hits.alert_id = %s",
                (day, alert_id, alert_id),
            )
    return [int(str(row[0])) for row in rows(seeded, "SELECT alert_id FROM core.alerts ORDER BY alert_id")]


@pytest.fixture
def cited_regulations(db: Database, regulations: list[str]) -> Iterator[list[str]]:
    yield regulations
    with db.owner() as conn:
        conn.execute("DELETE FROM corpus.passages")
        conn.execute("DELETE FROM corpus.documents")


@contextmanager
def app_client(db: Database, model: RuleScoreChatModel) -> Iterator[TestClient]:
    settings = Settings(
        owner_dsn=SecretStr(db.owner_dsn),
        mcp_dsn=SecretStr(db.mcp_dsn),
        app_dsn=SecretStr(db.app_dsn),
        evaluator_dsn=SecretStr(db.evaluator_dsn),
    )
    app = create_app(settings, mcp=build_server(db.mcp_dsn), model=model)
    with TestClient(app, follow_redirects=False) as client:
        yield client


@pytest.fixture
def client(seeded: Database) -> Iterator[TestClient]:
    with app_client(seeded, ESCALATING) as client:
        yield client


@pytest.fixture
def closing(seeded: Database) -> Iterator[TestClient]:
    with app_client(seeded, CLOSING) as client:
        yield client


def rows(db: Database, query: str, params: tuple[Any, ...] = ()) -> list[tuple[Any, ...]]:
    with db.owner() as conn:
        return conn.execute(query, params).fetchall()


def investigate(client: TestClient, db: Database, alert_id: int) -> int:
    response = client.post(f"/alerts/{alert_id}/investigate")
    assert response.status_code == 303
    assert response.headers["location"] == f"/alerts/{alert_id}"
    [(investigation_id,)] = rows(
        db, "SELECT max(investigation_id) FROM review.investigations WHERE alert_id = %s", (alert_id,)
    )
    return int(investigation_id)


def investigate_closing(
    closing: TestClient, db: Database, alert_id: int, monkeypatch: pytest.MonkeyPatch, *, sampled: bool
) -> int:
    monkeypatch.setattr(routing, "QA_RATE", 1.0 if sampled else 0.0)
    return investigate(closing, db, alert_id)


def test_all_alerts_lists_the_alert(client: TestClient, alert_id: int) -> None:
    page = client.get("/alerts").text

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
    assert "awaiting review" in client.get("/alerts").text

    for _ in range(2):
        response = client.post(
            f"/investigations/{investigation_id}/decision", data={"final_decision": "close", "comment": "payroll"}
        )
        assert response.status_code == 303
        assert response.headers["location"] == f"/alerts/{alert_id}"

    assert rows(seeded, "SELECT investigation_id, analyst, final_decision, comment FROM review.decisions") == [
        (investigation_id, "analyst", "close", "payroll")
    ]
    assert "decided: close" in client.get("/alerts").text
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


def test_queue_holds_escalations_and_qa_samples_and_auto_closed_lists_closures(
    client: TestClient,
    closing: TestClient,
    seeded: Database,
    alert_ids: list[int],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    escalated, sampled, closed = alert_ids[:3]
    investigate(client, seeded, escalated)
    investigate_closing(closing, seeded, sampled, monkeypatch, sampled=True)
    investigate_closing(closing, seeded, closed, monkeypatch, sampled=False)

    queue = client.get("/").text
    assert f'href="/alerts/{escalated}"' in queue
    assert f'href="/alerts/{sampled}"' in queue
    assert f'href="/alerts/{closed}"' not in queue
    assert '<span class="badge escalation">Escalation</span>' in queue
    assert '<span class="badge qa_sample">QA sample</span>' in queue
    assert "2 awaiting review: 1 escalation, 1 QA sample." in queue

    audit = client.get("/auto-closed").text
    assert f'href="/alerts/{escalated}"' not in audit
    assert f'href="/alerts/{sampled}"' in audit
    assert f'href="/alerts/{closed}"' in audit
    assert '<span class="badge pending">pending</span>' in audit
    assert "not sampled" in audit
    assert "2 auto-closed, 1 of them drawn as QA samples." in audit


@pytest.mark.usefixtures("cited_regulations")
def test_alert_page_links_citations_and_shows_trace(client: TestClient, seeded: Database, alert_id: int) -> None:
    investigate(client, seeded, alert_id)
    [(decision,)] = rows(seeded, "SELECT decision FROM review.investigations")
    claims = [claim for field in ("who", "what", "when", "where", "why", "how") for claim in decision[field]]
    txn_id = claims[0]["txn_ids"][0]
    passage_id = next(passage for claim in claims for passage in claim["passage_ids"])
    anchor = "passage-" + passage_id.replace(" ", "_")

    page = client.get(f"/alerts/{alert_id}").text

    assert '<span class="badge escalated">Escalated</span>' in page
    assert f'<a href="#txn-{txn_id}">{txn_id}</a>' in page
    assert f'<tr id="txn-{txn_id}">' in page
    assert f'<a href="#{anchor}">{passage_id}</a>' in page
    assert f'<div class="passage" id="{anchor}">' in page
    assert "<details>" in page
    assert "<td>get_alert</td>" in page
    assert "model finished" in page
    assert "Closed automatically" not in page
    assert "/qa-review" not in page


def test_qa_review_is_recorded_once(
    closing: TestClient, seeded: Database, alert_id: int, monkeypatch: pytest.MonkeyPatch
) -> None:
    investigation_id = investigate_closing(closing, seeded, alert_id, monkeypatch, sampled=True)

    page = closing.get(f"/alerts/{alert_id}").text
    assert '<span class="badge qa_sample">QA sample</span>' in page
    assert "Closed automatically" in page
    assert "<code>auto-close</code>" in page
    assert f'action="/investigations/{investigation_id}/qa-review"' in page
    assert 'name="final_decision"' not in page

    for outcome, comment in (("reopened", "funnel account"), ("confirmed", "second opinion")):
        response = closing.post(
            f"/investigations/{investigation_id}/qa-review", data={"outcome": outcome, "comment": comment}
        )
        assert response.status_code == 303
        assert response.headers["location"] == f"/alerts/{alert_id}"

    assert rows(seeded, "SELECT investigation_id, analyst, outcome, comment FROM review.qa_reviews") == [
        (investigation_id, "analyst", "reopened", "funnel account")
    ]
    page = closing.get(f"/alerts/{alert_id}").text
    assert "/qa-review" not in page
    assert '<span class="badge reopened">reopened</span>' in page
    assert f'href="/alerts/{alert_id}"' not in closing.get("/").text
    assert '<span class="badge reopened">reopened</span>' in closing.get("/auto-closed").text


def test_auto_closed_alert_has_no_forms_and_rejects_qa_review(
    client: TestClient,
    closing: TestClient,
    seeded: Database,
    alert_ids: list[int],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    closed, escalated = alert_ids[:2]
    auto_closed = investigate_closing(closing, seeded, closed, monkeypatch, sampled=False)
    escalation = investigate(client, seeded, escalated)

    page = closing.get(f"/alerts/{closed}").text

    assert '<span class="badge auto_closed">Auto-closed</span>' in page
    assert "no analyst decision was needed" in page
    assert 'name="final_decision"' not in page
    assert "/qa-review" not in page
    for investigation_id in (auto_closed, escalation, 999999):
        response = closing.post(f"/investigations/{investigation_id}/qa-review", data={"outcome": "confirmed"})
        assert response.status_code == 404
    assert rows(seeded, "SELECT count(*) FROM review.qa_reviews") == [(0,)]
