from datetime import date, timedelta
from pathlib import Path
from typing import Any

import pytest

from aml_investigator.evaluation.results import AlertTruth, GitState
from aml_investigator.evaluation.systems import EvalAlert, Recommendation, SystemDecision
from aml_investigator.simulation.laundering import GroupProgress
from aml_investigator.simulation.loop import Actor, Case, SimulationRun
from aml_investigator.simulation.results import (
    HeldOutAlreadyOpenedError,
    refuse_second_opening,
    result_document,
    result_path,
    summary,
    write_result,
)
from aml_investigator.simulation.world import live_days

WEEK_ONE = live_days()[2]
WEEK_THREE = live_days()[15]
GIT = GitState(commit="abc1234", dirty=False)


def make_case(
    alert_id: int,
    recommendation: Recommendation,
    *,
    truth: bool,
    persona: str = "student",
    group_id: int | None = None,
    shadow: Recommendation | None = None,
    decided_on: date = WEEK_ONE,
    cost: float | None = None,
) -> Case:
    account = f"A{alert_id}"
    return Case(
        EvalAlert(alert_id, account, decided_on),
        SystemDecision(recommendation, cost_usd=cost, latency_ms=100.0),
        recommendation,
        shadow,
        decided_on,
        AlertTruth(alert_id, truth, []),
        Actor(account, persona, group_id, "mules" if group_id is not None else None),
    )


GROUPS = (
    GroupProgress(0, "mules", "money_mule", WEEK_ONE, WEEK_ONE + timedelta(days=3), None, 900.0, 1800.0),
    GroupProgress(1, "invoices", "invoice_disguise", WEEK_ONE, None, None, 5000.0, 5000.0),
)


def make_run(cases: tuple[Case, ...], *, system: str = "azure", truncated: bool = False) -> SimulationRun:
    return SimulationRun(
        "aml_sim_dev",
        1,
        system,
        1,
        60,
        truncated,
        3 if truncated else 0,
        live_days()[-1],
        "hash",
        cases,
        GROUPS,
        {"A1": WEEK_ONE + timedelta(days=1)},
    )


@pytest.fixture
def workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.chdir(tmp_path)
    path = tmp_path / "config/thresholds/aml_sim_dev.json"
    path.parent.mkdir(parents=True)
    path.write_text("{}")
    path.with_name("aml_sim_dev.segments.json").write_text("{}")
    return tmp_path


CASES = (
    make_case(1, "escalate", truth=True, group_id=0, shadow="close", cost=0.1),
    make_case(2, "close", truth=False, shadow="escalate", cost=0.1),
    make_case(3, "close", truth=True, persona="corporation", group_id=1, shadow="escalate", cost=0.1),
    make_case(4, "escalate", truth=False, shadow="close", decided_on=WEEK_THREE, cost=0.1),
    make_case(5, "escalate", truth=False, shadow="escalate", persona="corporation", decided_on=WEEK_THREE, cost=0.1),
)


def document(cases: tuple[Case, ...] = CASES, **options: Any) -> dict[str, Any]:
    return result_document(make_run(cases, **options), GIT)


@pytest.mark.usefixtures("workspace")
def test_groups_found_days_and_money_include_zeros() -> None:
    result = document()

    assert result["counts"]["groups"] == 2
    assert result["counts"]["groups_found"] == 1
    by_scenario = {group["scenario"]: group for group in result["groups"]}
    assert by_scenario["mules"]["found"] is True
    assert by_scenario["mules"]["days_to_first_block"] == 3
    assert by_scenario["mules"]["sink_eur"] == 900.0
    assert by_scenario["mules"]["planned_sink_eur"] == 1800.0
    assert by_scenario["invoices"]["found"] is False
    assert by_scenario["invoices"]["days_to_first_block"] is None


@pytest.mark.usefixtures("workspace")
def test_rates_are_reported_overall_and_per_persona() -> None:
    result = document()

    assert result["metrics"]["alerts"] == 5
    assert result["metrics"]["close_rate"] == pytest.approx(2 / 5)
    assert result["metrics"]["false_close_rate"] == pytest.approx(1 / 2)
    assert result["metrics"]["escalation_precision"] == pytest.approx(1 / 3)
    assert result["by_persona"]["student"]["alerts"] == 3
    assert result["by_persona"]["corporation"]["false_close_rate"] == pytest.approx(1.0)


@pytest.mark.usefixtures("workspace")
def test_cost_and_latency_per_alert() -> None:
    result = document()

    assert result["metrics"]["mean_cost_usd"] == pytest.approx(0.1)
    assert result["metrics"]["total_cost_usd"] == pytest.approx(0.5)
    assert result["metrics"]["mean_latency_ms"] == pytest.approx(100.0)


@pytest.mark.usefixtures("workspace")
def test_workload_is_counted_per_weekly_round() -> None:
    result = document()

    assert result["workload"] == [
        {"round": 1, "alerts": 3, "escalations": 1},
        {"round": 2, "alerts": 0, "escalations": 0},
        {"round": 3, "alerts": 2, "escalations": 2},
        {"round": 4, "alerts": 0, "escalations": 0},
    ]


@pytest.mark.usefixtures("workspace")
def test_shadow_comparison_splits_all_differences() -> None:
    assert document()["shadow"] == {
        "alerts_compared": 5,
        "alerts_differing": 4,
        "laundering_caught_b4_closed": 1,
        "honest_closed_b4_escalated": 1,
        "laundering_closed_b4_escalated": 1,
        "honest_escalated_b4_closed": 1,
    }


@pytest.mark.usefixtures("workspace")
def test_a_free_run_has_no_shadow() -> None:
    cases = (make_case(1, "escalate", truth=True, group_id=0),)

    assert document(cases, system="b4")["shadow"] is None


@pytest.mark.usefixtures("workspace")
def test_truncation_is_marked() -> None:
    result = document(truncated=True)

    assert result["truncated"] is True
    assert result["undecided_alerts"] == 3
    assert "TRUNCATED" in summary(result)


@pytest.mark.usefixtures("workspace")
def test_summary_names_system_and_groups() -> None:
    text = summary(document())

    assert "azure" in text
    assert "1 of 2 groups" in text


@pytest.mark.parametrize(
    ("limit", "name"), [(60, "seed1-azure-cadence1.json"), (30, "seed1-azure-cadence1-first30.json")]
)
def test_result_path(limit: int, name: str) -> None:
    assert result_path("aml_sim_dev", 1, "azure", 1, limit) == Path("eval/results/aml_sim_dev/sim") / name


@pytest.mark.usefixtures("workspace")
def test_write_result_creates_the_file() -> None:
    path = write_result(make_run(CASES), GIT)

    assert path.exists()
    assert path.parent == Path("eval/results/aml_sim_dev/sim")


@pytest.mark.usefixtures("workspace")
def test_the_held_out_world_opens_once_for_an_agent() -> None:
    target = result_path("aml_sim_heldout", 3, "azure", 7, 30)

    refuse_second_opening("aml_sim_heldout", "azure")
    target.parent.mkdir(parents=True)
    target.write_text("{}")

    with pytest.raises(HeldOutAlreadyOpenedError, match="azure"):
        refuse_second_opening("aml_sim_heldout", "azure")
    refuse_second_opening("aml_sim_heldout", "azure_profile")
    refuse_second_opening("aml_sim_heldout", "b4")
    refuse_second_opening("aml_sim_dev", "azure")
