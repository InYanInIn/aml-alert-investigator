import json
from pathlib import Path
from typing import Any

import pytest

from aml_investigator.evaluation.report import (
    REPORT_END,
    REPORT_START,
    agreement_with,
    auto_closed,
    closure_estimates,
    render,
    update_results_document,
    wilson,
)

LOG = """# Test split log

| Date | Milestone | What was computed | Decisions taken from it |
|---|---|---|---|
| 2026-10-29 | M2 | Counted alerts. Unintended. | None. |
"""
RESULTS = "eval/results/aml_hi_small/val"


def alert_row(alert_id: int, truth: bool, recommendation: str, verification: str | None = "passed") -> dict[str, Any]:
    return {
        "alert_id": alert_id,
        "escalate_truth": truth,
        "recommendation": recommendation,
        "verification_status": verification,
    }


def estimate(value: float) -> dict[str, float]:
    return {"value": value, "low": value / 2, "high": value}


def result_document(system: str, alerts: list[dict[str, Any]], commit: str = "abcdef1234") -> dict[str, Any]:
    names = [
        "close_rate",
        "false_close_rate",
        "escalation_precision",
        "mean_cost_usd",
        "p50_latency_ms",
        "recall_pattern",
        "recall_other",
        "typology_hit_rate",
        "p95_latency_ms",
    ]
    return {
        "system": system,
        "git_commit": commit,
        "git_dirty": system == "b3",
        "counts": {"alerts": len(alerts), "population": {"positive": 2, "negative": 2}},
        "metrics": {"weighted": {name: estimate(0.25) for name in names}},
        "alerts": alerts,
    }


def pairs_document(commit: str, escalated: int, succeeded: int) -> dict[str, Any]:
    counts = {"pairs": 8, "escalated_clean": escalated, "attack_succeeded": succeeded}
    return {"git_commit": commit, "git_dirty": False, "counts": counts}


def write(workspace: Path, relative: str, document: dict[str, Any]) -> None:
    path = workspace / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(document))


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "test-split-log.md").write_text(LOG)
    (tmp_path / "docs" / "results.md").write_text("# Results\n\nHand-written.\n")
    return tmp_path


TOY_AGENT = [
    alert_row(1, False, "close"),
    alert_row(2, False, "close"),
    alert_row(3, True, "close"),
    alert_row(4, True, "escalate"),
]
TOY_B3 = [
    alert_row(1, False, "close", None),
    alert_row(2, False, "escalate", None),
    alert_row(3, True, "close", None),
    alert_row(4, True, "close", None),
]


def test_tables_show_present_files_missing_files_and_exclude_partial_runs(workspace: Path) -> None:
    write(workspace, f"{RESULTS}/b0.json", result_document("b0", TOY_AGENT))
    write(workspace, f"{RESULTS}/b3.json", result_document("b3", TOY_B3))
    write(workspace, f"{RESULTS}/azure-first10.json", result_document("azure", TOY_AGENT))

    text = render(workspace)

    section = text.split("### aml_hi_small, val split")[1].split("### aml_hi_small, test split")[0]
    assert "| b0 | 4 | 25.0% [12.5, 25.0] |" in section
    assert "| azure | not run |" in section
    assert "azure-first10" not in text
    assert f"`{RESULTS}/b0.json`: commit `abcdef1`, clean" in section
    assert f"`{RESULTS}/b3.json`: commit `abcdef1`, dirty" in section
    test_section = text.split("### aml_hi_small, test split")[1].split("### aml_li_small")[0]
    assert "| b0 | not run |" in test_section
    assert "Sources: none." in test_section


def test_agreement_variant_closes_only_where_b3_also_closes() -> None:
    document = result_document("azure", TOY_AGENT)

    alone, alone_false = closure_estimates(document, auto_closed)
    agreed, agreed_false = closure_estimates(document, agreement_with(result_document("b3", TOY_B3)))

    assert alone.value == pytest.approx(3 / 4)
    assert alone_false.value == pytest.approx(1 / 3)
    assert agreed.value == pytest.approx(2 / 4)
    assert agreed_false.value == pytest.approx(1 / 2)


def test_failed_verification_is_not_an_auto_close() -> None:
    assert not auto_closed(alert_row(1, False, "close", "failed"))
    assert auto_closed(alert_row(1, False, "close", "passed"))


def test_auto_close_table_reports_agreement_and_missing_baseline(workspace: Path) -> None:
    write(workspace, f"{RESULTS}/azure.json", result_document("azure", TOY_AGENT))
    write(workspace, f"{RESULTS}/b3.json", result_document("b3", TOY_B3))
    write(workspace, "eval/results/aml_li_small/val/azure.json", result_document("azure", TOY_AGENT))

    text = render(workspace)

    hi = text.split("### Auto-close (ADR-0017), aml_hi_small, val split")[1].split("###")[0]
    assert "| azure | 4 | 75.0%" in hi
    assert "50.0%" in hi
    li = text.split("### Auto-close (ADR-0017), aml_li_small, val split")[1].split("###")[0]
    assert "B3 result for this split is missing" in li


def test_wilson_interval_on_a_toy_example() -> None:
    low, high = wilson(1, 6)

    assert low == pytest.approx(0.0301, abs=1e-3)
    assert high == pytest.approx(0.5635, abs=1e-3)
    assert wilson(0, 10)[0] == 0.0
    assert wilson(10, 10)[1] == 1.0


def test_pairs_table_shows_attack_success_and_control(workspace: Path) -> None:
    write(workspace, f"{RESULTS}/injection-pairs/azure.json", pairs_document("a" * 10, 6, 1))
    write(workspace, f"{RESULTS}/injection-pairs/azure-control.json", pairs_document("b" * 10, 6, 0))

    text = render(workspace)

    assert "| azure | 8 | 1 / 6 = 16.7% [3.0%, 56.4%] | 0 / 6 = 0.0% [0.0%, 39.0%] |" in text
    assert "azure-control.json`: commit `bbbbbbb`" in text


def test_openings_are_listed_from_the_log(workspace: Path) -> None:
    assert "| 2026-10-29 | M2 | Counted alerts. Unintended. | None. |" in render(workspace)


def test_retrieval_table_comes_from_the_result_file(workspace: Path) -> None:
    scores = {"hit@5": 0.5, "mrr": 0.25}
    per_law = {"AMLR": {"queries": 4, **dict.fromkeys(("fts", "vector", "hybrid"), scores)}}
    document = {
        "git_commit": "c" * 10,
        "git_dirty": False,
        "metrics_unfiltered": per_law,
        "metrics_per_document": per_law,
    }
    write(workspace, "eval/results/aml_hi_small/retrieval.json", document)

    assert "| AMLR (4) | 0.50 / 0.25 | 0.50 / 0.25 | 0.50 / 0.25 | 0.50 / 0.25 |" in render(workspace)


def test_updating_the_document_creates_the_section_and_is_idempotent(workspace: Path) -> None:
    path = workspace / "docs" / "results.md"

    update_results_document(workspace)
    first = path.read_text()
    update_results_document(workspace)

    assert path.read_text() == first
    assert first.startswith("# Results\n\nHand-written.\n")
    assert "## M8 — evaluation" in first
    assert first.count(REPORT_START) == first.count(REPORT_END) == 1


def test_updating_replaces_only_the_marked_block(workspace: Path) -> None:
    path = workspace / "docs" / "results.md"
    path.write_text(f"intro\n{REPORT_START}\nstale\n{REPORT_END}\noutro\n")

    update_results_document(workspace)

    text = path.read_text()
    assert text.startswith(f"intro\n{REPORT_START}\n")
    assert text.endswith(f"{REPORT_END}\noutro\n")
    assert "stale" not in text
