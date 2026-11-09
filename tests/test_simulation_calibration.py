import json
from pathlib import Path

import pytest

from aml_investigator.simulation.loop import calibrate_world
from aml_investigator.simulation.world import FIRST_DAY, NotASimulationDatabaseError
from tests.conftest import Database
from tests.simulation_fixtures import THRESHOLDS, settings_for, write_config


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.mark.anyio
async def test_calibration_writes_thresholds_and_the_b1_operating_point(
    world_db: Database, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = settings_for(world_db)
    write_config(tmp_path / "config" / "simulation")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("aml_investigator.simulation.loop.choose_thresholds", lambda *_, **__: THRESHOLDS)

    thresholds_path, point_path = await calibrate_world(settings, seed=61, budget_per_day=2.0)

    calibrated = json.loads(thresholds_path.read_text())
    point = json.loads(point_path.read_text())["operating_point"]
    assert thresholds_path == Path(f"config/thresholds/{settings.database}.json")
    assert point_path == Path(f"eval/results/{settings.database}/val/b1.json")
    assert calibrated["budget_per_day"] == 2.0
    assert calibrated["suppression_days"] == 3
    assert calibrated["calibrated_on"]["first"] == FIRST_DAY.isoformat()
    assert calibrated["thresholds"] == THRESHOLDS
    assert set(point) == {"threshold", "close_rate", "false_close_rate"}


@pytest.mark.anyio
async def test_calibration_refuses_any_other_database(db: Database) -> None:
    with pytest.raises(NotASimulationDatabaseError):
        await calibrate_world(settings_for(db), seed=61, budget_per_day=2.0)
