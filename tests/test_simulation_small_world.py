from pathlib import Path

import pytest

from aml_investigator.evaluation.results import GitState
from aml_investigator.simulation.loop import simulate
from aml_investigator.simulation.results import update_simulation_report, write_result
from tests.conftest import Database, load_regulations
from tests.simulation_fixtures import prepare_workspace, settings_for


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.mark.anyio
async def test_a_small_world_runs_end_to_end_with_b4_and_the_scripted_system(
    world_db: Database, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = settings_for(world_db)
    prepare_workspace(tmp_path, settings.database)
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs/results.md").write_text("# Results\n")
    monkeypatch.chdir(tmp_path)
    load_regulations(world_db)
    git_state = GitState(commit="abc1234", dirty=False)

    for system in ("b4", "scripted"):
        run = await simulate(settings, seed=51, system=system, cadence=1)
        assert run.cases
        assert run.groups
        path = write_result(run, git_state)
        assert path.exists()

    with world_db.owner() as conn:
        investigated = conn.execute("SELECT count(*) FROM review.investigations").fetchone()
        external = conn.execute(
            "SELECT count(*) FROM core.alerts a JOIN core.accounts c USING (account_id) WHERE c.bank_id <> 1"
        ).fetchone()
    assert investigated is not None
    assert int(str(investigated[0])) > 0
    assert external == (0,)
    assert update_simulation_report(tmp_path).exists()
