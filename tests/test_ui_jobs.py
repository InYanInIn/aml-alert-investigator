import asyncio
import json
import shutil
import subprocess
from decimal import Decimal
from pathlib import Path
from typing import Any, Literal

import httpx
import pytest
from fastapi import FastAPI

from aml_investigator.core.logging import JsonFormatter
from aml_investigator.investigation.agent.nodes import NO_DECISION
from aml_investigator.investigation.models import azure
from aml_investigator.web.jobs import AlertProgress, JobConflictError, SimulationJobs
from aml_investigator.web.routes.alerts import rerun_router
from tests.conftest import Database, load_regulations
from tests.simulation_fixtures import prepare_workspace, settings_for
from tests.test_azure_model import completion, tool_call_message
from tests.test_simulation_resume import finished

ENDPOINT = "https://aml-investigator-01234567.openai.azure.com"
EMBEDDING = [1.0] + [0.0] * 1535


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
def azure_auth(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(azure, "DefaultAzureCredential", lambda: None)
    monkeypatch.setattr(azure, "get_bearer_token_provider", lambda *_: lambda: "offline-test-token")


@pytest.fixture
def azure_deployments(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, str | None]]:
    deployments: list[dict[str, str | None]] = [
        {
            "deployment": model,
            "format": "OpenAI",
            "model": model,
            "version": version,
            "sku": sku,
            "state": "Succeeded",
            "spillover": None,
            "service_tier": "Default" if sku == "GlobalStandard" else None,
        }
        for model, version, sku in (
            ("gpt-5-mini", "2025-08-07", "GlobalStandard"),
            ("text-embedding-3-small", "1", "DataZoneStandard"),
        )
    ]

    def run(arguments: list[str], **options: object) -> subprocess.CompletedProcess[str]:
        assert arguments[0] == "/test/az"
        assert arguments[1:3] == ["cognitiveservices", "account"]
        assert "rg-aml-investigator" in arguments
        assert "aml-investigator-openai" in arguments
        assert options["stderr"] == subprocess.DEVNULL
        assert options["timeout"] == 10
        if arguments[3] == "show":
            payload: object = {
                "name": "aml-investigator-openai",
                "kind": "OpenAI",
                "endpoint": f"{ENDPOINT}/",
                "subdomain": "aml-investigator-01234567",
                "state": "Succeeded",
            }
        else:
            assert arguments[3:5] == ["deployment", "list"]
            includes_tier = "service_tier:properties.serviceTier" in arguments[-1]
            payload = [
                {key: value for key, value in deployment.items() if key != "service_tier" or includes_tier}
                for deployment in deployments
            ]
        return subprocess.CompletedProcess(arguments, 0, json.dumps(payload))

    monkeypatch.setattr(shutil, "which", lambda _: "/test/az")
    monkeypatch.setattr(subprocess, "run", run)
    return deployments


def fake_embedding_response() -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "object": "list",
            "data": [{"index": 0, "object": "embedding", "embedding": EMBEDDING}],
            "model": "text-embedding-3-small",
            "usage": {"prompt_tokens": 3, "total_tokens": 3},
        },
    )


def fake_chat_response(body: dict[str, Any]) -> httpx.Response:
    assert body["max_completion_tokens"] == 8192
    tools = {tool["function"]["name"] for tool in body.get("tools", [])}
    message = tool_call_message(NO_DECISION.model_dump_json()) if "Decision" in tools else {"content": "Done."}
    return httpx.Response(200, json=completion(message))


@pytest.mark.anyio
@pytest.mark.usefixtures("azure_deployments", "azure_auth")
@pytest.mark.parametrize("verification_status", ["failed", "passed"])
async def test_rerun_charges_new_requests_without_republishing_the_alert(
    world_db: Database, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, verification_status: str
) -> None:
    prepare_workspace(tmp_path, "aml_sim_dev")
    monkeypatch.chdir(tmp_path)
    load_regulations(world_db)
    entered, release = asyncio.Event(), asyncio.Event()
    pause = False

    async def handler(request: httpx.Request) -> httpx.Response:
        if pause:
            entered.set()
            await release.wait()
        if request.url.path.endswith("/embeddings"):
            return fake_embedding_response()
        return httpx.Response(200, json=completion({"content": "No decision."}))

    settings = settings_for(world_db).model_copy(update={"azure_openai_endpoint": ENDPOINT})
    jobs = SimulationJobs(settings, azure_transport=httpx.MockTransport(handler))
    await jobs.create(51, {})
    initial = await finished(jobs, await jobs.start(7, "azure_profile"))
    assert initial.status == "done"

    with world_db.owner() as conn:
        rows = conn.execute("SELECT alert_id, investigation_id FROM review.investigations ORDER BY investigation_id")
        candidate = rows.fetchone()
        assert candidate is not None
        alert_id, investigation_id = candidate
        if verification_status == "passed":
            verified = conn.execute(
                "INSERT INTO review.investigations "
                "(alert_id, thread_id, model, prompt_version, recommendation, decision, verification, route, trace) "
                "SELECT alert_id, thread_id || ':verified', model, prompt_version, recommendation, decision, "
                "jsonb_build_object('status', 'passed', 'errors', jsonb_build_array()), route, trace "
                "FROM review.investigations WHERE investigation_id = %s RETURNING investigation_id",
                (investigation_id,),
            ).fetchone()
            assert verified is not None
            investigation_id = verified[0]
        original_attempts = conn.execute(
            "SELECT investigation_id FROM review.investigations WHERE alert_id = %s ORDER BY investigation_id",
            (alert_id,),
        ).fetchall()
        original_decisions = conn.execute("SELECT * FROM sim.decision ORDER BY alert_id").fetchall()
        original_blocks = conn.execute("SELECT * FROM sim.block ORDER BY account_id").fetchall()
        original_ordinals = conn.execute(
            "SELECT * FROM sim.azure_spend ORDER BY account_id, alert_date, ordinal"
        ).fetchall()
    spent = jobs.spent()

    app = FastAPI()
    app.state.simulation_jobs = jobs
    app.include_router(rerun_router)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver") as client:
        endpoint = f"/alerts/{alert_id}/rerun"
        body = {"investigation_id": investigation_id}
        assert (await client.post(endpoint, data=body)).status_code == 422
        pause = True
        rerun = asyncio.create_task(client.post(endpoint, json=body))
        try:
            await asyncio.wait_for(entered.wait(), timeout=5)
            assert (await client.get(f"{endpoint}-status")).json() == {
                "running": True,
                "investigation_id": investigation_id,
            }
        finally:
            release.set()
            response = await rerun
        assert response.status_code == 204
        completed = (await client.get(f"{endpoint}-status")).json()
        assert not completed["running"]
        assert completed["investigation_id"] > investigation_id
        assert (await client.post(endpoint, json=body)).status_code == 409

    assert jobs.spent() > spent
    assert jobs.current() == initial
    with world_db.owner() as conn:
        assert conn.execute("SELECT * FROM sim.decision ORDER BY alert_id").fetchall() == original_decisions
        assert conn.execute("SELECT * FROM sim.block ORDER BY account_id").fetchall() == original_blocks
        attempts = conn.execute(
            "SELECT investigation_id FROM review.investigations WHERE alert_id = %s ORDER BY investigation_id",
            (alert_id,),
        ).fetchall()
        assert len(attempts) == len(original_attempts) + 1
        assert attempts[:-1] == original_attempts
        ledger = conn.execute("SELECT * FROM sim.azure_spend ORDER BY account_id, alert_date, ordinal").fetchall()
        assert len(ledger) > len(original_ordinals)
        assert set(original_ordinals) <= set(ledger)


@pytest.mark.anyio
@pytest.mark.usefixtures("azure_deployments", "azure_auth")
@pytest.mark.parametrize("resume_system", ["azure_profile", "b4"])
async def test_azure_budget_accounts_for_both_kinds_and_skips_an_uncertain_alert_after_restart(
    world_db: Database,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    resume_system: Literal["azure_profile", "b4"],
) -> None:
    prepare_workspace(tmp_path, "aml_sim_dev")
    monkeypatch.chdir(tmp_path)
    load_regulations(world_db)
    with world_db.owner() as conn:
        conn.execute("TRUNCATE sim.azure_spend")
    requested: list[str] = []
    observed_progress: list[AlertProgress | None] = []
    interrupt_embedding = True

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal interrupt_embedding
        body = json.loads(request.content)
        requested.append(request.url.path)
        if interrupt_embedding:
            current = jobs.current()
            assert current is not None
            observed_progress.append(current.alert_progress)
        if request.url.path.endswith("/embeddings"):
            assert len(body["input"]) == 1
            if interrupt_embedding:
                interrupt_embedding = False
                raise httpx.ReadTimeout("private provider details")
            return fake_embedding_response()
        assert request.url.path.endswith("/chat/completions")
        return fake_chat_response(body)

    settings = settings_for(world_db).model_copy(update={"azure_openai_endpoint": ENDPOINT})
    low_limit = settings.model_copy(update={"ui_spend_limit_usd": Decimal("0.001")})
    rejected = SimulationJobs(low_limit, azure_transport=httpx.MockTransport(handler))
    await rejected.create(51, {})
    estimate = rejected.estimate(7, "azure_profile")
    assert estimate.cap > 0
    assert estimate.cost_usd > 0
    assert not estimate.within_limit
    assert rejected.estimate(7, "b4").within_limit
    with pytest.raises(JobConflictError, match="spend limit"):
        await rejected.start(7, "azure_profile")
    assert requested == []

    jobs = SimulationJobs(settings, azure_transport=httpx.MockTransport(handler))
    failed = await finished(jobs, await jobs.start(7, "azure_profile"))
    assert failed.status == "failed"
    assert failed.error == "The Azure request failed."
    assert failed.investigations == 0
    assert len(observed_progress) == 2
    progress = observed_progress[0]
    assert progress is not None
    assert observed_progress[1] == progress
    assert progress.number == 1
    assert progress.total >= 1
    assert failed.alert_progress is None
    assert all("private provider details" not in JsonFormatter().format(record) for record in caplog.records)
    assert [path.rsplit("/", 1)[-1] for path in requested] == ["completions", "embeddings"]
    with world_db.owner() as conn:
        rows = conn.execute(
            "SELECT generation, account_id, alert_date, kind, reserved_usd, settled_usd "
            "FROM sim.azure_spend ORDER BY ordinal"
        ).fetchall()
    assert len(rows) == 2
    generation, account, alert_date, kind, reserved, settled = rows[0]
    assert (kind, reserved, settled) == ("chat", Decimal("0.1012608"), Decimal("0.000015"))
    assert rows[1] == (generation, account, alert_date, "embedding", Decimal("0.00024576"), None)
    assert jobs.spent() == pytest.approx(0.00026076)

    restarted = SimulationJobs(settings, azure_transport=httpx.MockTransport(handler))
    restarted.recover()
    sent_before_resume = len(requested)
    resumed = await finished(restarted, await restarted.start(7, resume_system))
    assert resumed.status == "done"
    assert resumed.days_completed == 7
    with world_db.owner() as conn:
        assert conn.execute(
            "SELECT skip_reason FROM sim.decision JOIN core.alerts USING (alert_id) "
            "WHERE account_id = %s AND alert_date = %s",
            (account, alert_date),
        ).fetchone() == ("prior_paid_attempt",)
        assert conn.execute(
            "SELECT count(*) FROM sim.azure_spend WHERE generation = %s AND account_id = %s AND alert_date = %s",
            (generation, account, alert_date),
        ).fetchone() == (2,)
        if resume_system == "azure_profile":
            assert conn.execute(
                "SELECT EXISTS (SELECT 1 FROM sim.azure_spend WHERE kind = 'embedding' AND settled_usd = 0.00000009)"
            ).fetchone() == (True,)
    if resume_system == "b4":
        assert len(requested) == sent_before_resume

    spent = restarted.spent()
    await restarted.create(51, {})
    assert restarted.spent() == spent
    with world_db.owner() as conn:
        assert conn.execute("SELECT generation FROM sim.world").fetchone() != (generation,)


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("deployment", "field", "value"),
    [(1, "version", "unexpected-version"), (0, "service_tier", "Priority")],
    ids=["unknown_version", "priority"],
)
async def test_unverified_deployment_rejects_the_paid_job_before_any_send(
    world_db: Database,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    azure_deployments: list[dict[str, str | None]],
    deployment: int,
    field: str,
    value: str,
) -> None:
    prepare_workspace(tmp_path, "aml_sim_dev")
    monkeypatch.chdir(tmp_path)
    azure_deployments[deployment][field] = value
    requested: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requested.append(request)
        return httpx.Response(500)

    settings = settings_for(world_db).model_copy(update={"azure_openai_endpoint": ENDPOINT})
    jobs = SimulationJobs(settings, azure_transport=httpx.MockTransport(handler))
    await jobs.create(51, {})
    try:
        with pytest.raises(JobConflictError, match="deployment metadata"):
            await jobs.start(7, "azure_profile")
    finally:
        await jobs.close()
    assert requested == []
    status = jobs.current()
    assert status is not None
    assert status.period_id == 0


@pytest.mark.anyio
@pytest.mark.usefixtures("azure_deployments", "azure_auth")
async def test_azure_does_not_send_or_reserve_after_mcp_sql_fails(
    world_db: Database, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    prepare_workspace(tmp_path, "aml_sim_dev")
    monkeypatch.chdir(tmp_path)
    load_regulations(world_db)
    requested: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        operation = request.url.path.rsplit("/", 1)[-1]
        requested.append(operation)
        return (
            fake_embedding_response() if operation == "embeddings" else fake_chat_response(json.loads(request.content))
        )

    settings = settings_for(world_db).model_copy(update={"azure_openai_endpoint": ENDPOINT})
    jobs = SimulationJobs(settings, azure_transport=httpx.MockTransport(handler))
    await jobs.create(51, {})
    with world_db.owner() as conn:
        conn.execute("TRUNCATE sim.azure_spend")
        conn.execute("INSERT INTO corpus.documents VALUES ('AMLR', 'AMLR', 'en', '2024', 'https://example.test', 'x')")
        conn.execute(
            "UPDATE corpus.passages SET doc_id = 'AMLR', embedding = %s::vector WHERE doc_id = 'amlr'",
            (str(EMBEDDING),),
        )
        conn.execute(
            "CREATE FUNCTION corpus.reject_passage_read() RETURNS boolean LANGUAGE plpgsql AS "
            "$$ BEGIN RAISE EXCEPTION 'private corpus database detail'; END $$"
        )
        conn.execute("ALTER TABLE corpus.passages ENABLE ROW LEVEL SECURITY")
        conn.execute(
            "CREATE POLICY reject_passage_read ON corpus.passages FOR SELECT TO aml_mcp_reader "
            "USING (corpus.reject_passage_read())"
        )
    try:
        failed = await finished(jobs, await jobs.start(7, "azure_profile"))
        assert failed.status == "failed"
        assert failed.investigations == 0
        assert requested == ["completions", "embeddings"]
        assert all("private corpus database detail" not in JsonFormatter().format(record) for record in caplog.records)
        with world_db.owner() as conn:
            assert conn.execute("SELECT kind, settled_usd FROM sim.azure_spend ORDER BY ordinal").fetchall() == [
                ("chat", Decimal("0.000015")),
                ("embedding", Decimal("0.00000009")),
            ]
            assert conn.execute("SELECT count(*) FROM review.investigations").fetchone() == (0,)
    finally:
        with world_db.owner() as conn:
            conn.execute("DROP POLICY reject_passage_read ON corpus.passages")
            conn.execute("ALTER TABLE corpus.passages DISABLE ROW LEVEL SECURITY")
            conn.execute("DROP FUNCTION corpus.reject_passage_read()")
