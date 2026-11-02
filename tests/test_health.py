import pytest
from starlette.testclient import TestClient as StarletteClient

from aml_investigator.core import health
from aml_investigator.core.db import open_read_only_pool
from aml_investigator.mcp_server.server import build_server
from tests.conftest import Database
from tests.test_api import ESCALATING, app_client


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def test_api_is_live_and_ready(db: Database) -> None:
    with app_client(db, ESCALATING) as client:
        assert client.get("/healthz").json() == {"status": "ok"}
        ready = client.get("/readyz")

    assert ready.status_code == 200
    assert ready.json() == {"status": "ready"}


def test_mcp_server_is_live_and_ready(db: Database) -> None:
    with StarletteClient(build_server(db.mcp_dsn).http_app()) as client:
        assert client.get("/healthz").json() == {"status": "ok"}
        ready = client.get("/readyz")

    assert ready.status_code == 200
    assert ready.json() == {"status": "ready"}


@pytest.mark.anyio
async def test_readiness_is_503_when_no_connection_answers_in_time(
    db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(health, "PING_TIMEOUT_SECONDS", 0.2)
    async with open_read_only_pool(db.mcp_dsn) as pool:
        await pool.resize(min_size=1, max_size=1)
        async with pool.connection():
            response = await health.readiness(pool)

    assert response.status_code == 503


@pytest.mark.anyio
async def test_readiness_is_503_before_the_pool_exists() -> None:
    assert (await health.readiness(None)).status_code == 503
