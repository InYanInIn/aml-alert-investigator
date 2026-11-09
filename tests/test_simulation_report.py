import json
from dataclasses import replace
from pathlib import Path

import pytest

from aml_investigator.simulation.results import (
    REPORT_END,
    REPORT_START,
    SPREAD_DATABASE,
    render_simulation,
    result_document,
    update_simulation_report,
)
from tests.test_simulation_results import CASES, GIT, make_run


@pytest.fixture
def workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs/results.md").write_text("# Results\n\nM8 stays.\n")
    for database in ("aml_sim_dev", "aml_sim_heldout", SPREAD_DATABASE):
        thresholds = tmp_path / f"config/thresholds/{database}.json"
        thresholds.parent.mkdir(parents=True, exist_ok=True)
        thresholds.write_text("{}")
    return tmp_path


def store(workspace: Path, name: str, database: str, seed: int, system: str, cadence: int, **changes: object) -> None:
    run = replace(make_run(CASES, system=system), database=database, seed=seed, cadence=cadence)
    document = {**result_document(run, GIT), **changes}
    path = workspace / "eval/results" / database / "sim" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(document))


def test_every_scenario_appears_including_zero_detection(workspace: Path) -> None:
    store(workspace, "seed1-azure-cadence1.json", "aml_sim_heldout", 1, "azure", 1)

    report = render_simulation(workspace)

    assert "mules" in report
    assert "invoices" in report
    assert "not found" in report


def test_the_report_shows_personas_workload_and_shadow(workspace: Path) -> None:
    store(workspace, "seed1-azure-cadence1.json", "aml_sim_heldout", 1, "azure", 1)

    report = render_simulation(workspace)

    assert "corporation" in report
    assert "Week 3" in report
    assert "Laundering caught that B4 closed" in report
    assert "Honest customers closed that B4 escalated" in report


def test_a_truncated_run_is_marked(workspace: Path) -> None:
    store(workspace, "seed1-azure-cadence1.json", "aml_sim_heldout", 1, "azure", 1, truncated=True)

    assert "truncated at " in render_simulation(workspace)


def test_partial_tuning_runs_are_not_tabulated(workspace: Path) -> None:
    store(workspace, "seed1-azure_profile-cadence1-first30.json", "aml_sim_dev", 1, "azure_profile", 1, limit=30)

    assert "azure_profile" not in render_simulation(workspace)


def test_free_systems_get_percentile_ranges_over_seeds(workspace: Path) -> None:
    for seed in range(1, 21):
        store(workspace, f"seed{seed}-b4-cadence3.json", SPREAD_DATABASE, seed, "b4", 3)

    report = render_simulation(workspace)

    assert "| b4 | 3 | 20 |" in report
    assert "[" in report.split("Spread over seeds")[1]


def test_missing_worlds_are_stated(workspace: Path) -> None:
    assert "not run" in render_simulation(workspace)


def test_the_section_is_generated_between_its_own_markers(workspace: Path) -> None:
    store(workspace, "seed1-azure-cadence1.json", "aml_sim_heldout", 1, "azure", 1)

    path = update_simulation_report(workspace)
    first = path.read_text()
    update_simulation_report(workspace)

    assert path.read_text() == first
    assert first.count(REPORT_START) == 1
    assert first.count(REPORT_END) == 1
    assert first.startswith("# Results\n\nM8 stays.\n")
    assert "## M9 — behaviour simulator" in first


def test_it_says_what_the_result_is_not(workspace: Path) -> None:
    assert "does not estimate accuracy on real bank data" in render_simulation(workspace)
