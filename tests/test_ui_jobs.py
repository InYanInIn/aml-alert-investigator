import json
from decimal import Decimal
from pathlib import Path

import httpx
import pytest

from aml_investigator.core.logging import JsonFormatter
from aml_investigator.investigation.agent.nodes import NO_DECISION
from aml_investigator.investigation.models import azure
from aml_investigator.web.jobs import JobConflictError, SimulationJobs
from tests.conftest import Database, load_regulations
from tests.simulation_fixtures import prepare_workspace, settings_for
from tests.test_azure_model import completion, tool_call_message
from tests.test_simulation_resume import finished

ENDPOINT = "https://aml-investigator-01234567.openai.azure.com"


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.mark.anyio
async def test_azure_budget_accounts_for_both_kinds_and_skips_an_uncertain_alert_after_restart(
    world_db: Database, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    prepare_workspace(tmp_path, "aml_sim_dev")
    monkeypatch.chdir(tmp_path)
    load_regulations(world_db)
    with world_db.owner() as conn:
        conn.execute("TRUNCATE sim.azure_spend")
    monkeypatch.setattr(azure, "DefaultAzureCredential", lambda: None)
    monkeypatch.setattr(azure, "get_bearer_token_provider", lambda *_: lambda: "offline-test-token")

    requested: list[str] = []
    interrupt_embedding = True

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal interrupt_embedding
        body = json.loads(request.content)
        requested.append(request.url.path)
        if request.url.path.endswith("/embeddings"):
            assert len(body["input"]) == 1
            if interrupt_embedding:
                interrupt_embedding = False
                raise httpx.ReadTimeout("private provider details")
            return httpx.Response(
                200,
                json={
                    "object": "list",
                    "data": [{"index": 0, "object": "embedding", "embedding": [1.0] + [0.0] * 1535}],
                    "model": "text-embedding-3-small",
                    "usage": {"prompt_tokens": 3, "total_tokens": 3},
                },
            )
        assert request.url.path.endswith("/chat/completions")
        assert body["max_completion_tokens"] == 8192
        tools = {tool["function"]["name"] for tool in body.get("tools", [])}
        message = tool_call_message(NO_DECISION.model_dump_json()) if "Decision" in tools else {"content": "Done."}
        return httpx.Response(200, json=completion(message))

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
        rejected.start(7, "azure_profile")
    assert requested == []

    jobs = SimulationJobs(settings, azure_transport=httpx.MockTransport(handler))
    failed = await finished(jobs, jobs.start(7, "azure_profile"))
    assert failed.status == "failed"
    assert failed.error == "The Azure request failed."
    assert failed.investigations == 0
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
    resumed = await finished(restarted, restarted.start(7, "azure_profile"))
    assert resumed.status == "done"
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
        assert conn.execute(
            "SELECT EXISTS (SELECT 1 FROM sim.azure_spend WHERE kind = 'embedding' AND settled_usd = 0.00000009)"
        ).fetchone() == (True,)

    spent = restarted.spent()
    await restarted.create(51, {})
    assert restarted.spent() == spent
    with world_db.owner() as conn:
        assert conn.execute("SELECT generation FROM sim.world").fetchone() != (generation,)
