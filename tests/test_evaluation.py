import json
from datetime import date, datetime, timedelta
from pathlib import Path

import psycopg
import pytest
from psycopg.conninfo import conninfo_to_dict

from aml_investigator.core.errors import NotFoundError
from aml_investigator.core.settings import Settings, load_settings
from aml_investigator.evaluation.metrics import (
    Outcome,
    ScoredAlert,
    choose_operating_point,
    measure,
    operating_point_at,
    report,
    stratum_weights,
)
from aml_investigator.evaluation.openings import ClosedTestSplitError
from aml_investigator.evaluation.runner import evaluate_system, resolve_alerts
from aml_investigator.evaluation.samples import AlertKey, Strata, create_sample, load_sample
from aml_investigator.evaluation.systems import EvalAlert, SystemDecision
from tests.conftest import Database

VAL_DAY = date(2022, 9, 7)
TEST_DAY = date(2022, 9, 9)
VAL_POSITIVES = {f"V{i:02}" for i in range(6)}
TEST_POSITIVES = {f"T{i}" for i in range(3)}
LOG_HEADER = (
    "# Test split log\n\n"
    "| Date | Milestone | What was computed | Decisions taken from it |\n|---|---|---|---|\n\nFooter.\n"
)


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def add_account(conn: psycopg.Connection[tuple[object, ...]], account: str) -> None:
    conn.execute("INSERT INTO core.accounts VALUES (%s, 1, 'Bank', %s, 'Corporation')", (account, account))


def add_labelled_alert(
    conn: psycopg.Connection[tuple[object, ...]],
    account: str,
    alert_date: date,
    *,
    escalate: bool,
    typologies: list[str],
) -> None:
    add_account(conn, account)
    window_end = datetime.combine(alert_date, datetime.min.time())
    row = conn.execute(
        "INSERT INTO core.alerts (account_id, alert_date, window_start, window_end) "
        "VALUES (%s, %s, %s, %s) RETURNING alert_id",
        (account, alert_date, window_end - timedelta(days=2), window_end),
    ).fetchone()
    assert row is not None
    conn.execute("INSERT INTO truth.alert_truth VALUES (%s, %s, %s, %s)", (row[0], escalate, escalate, typologies))


@pytest.fixture
def populated(db: Database) -> Database:
    with db.owner() as conn:
        add_account(conn, "S1")
        add_account(conn, "S2")
        for txn_id, day in ((1, date(2022, 9, 1)), (2, date(2022, 9, 10))):
            conn.execute(
                "INSERT INTO core.transactions VALUES (%s, %s, 'S1', 'S2', 100, 'Euro', 100, 'Euro', 'ACH', 100)",
                (txn_id, day),
            )
        for i in range(30):
            account = f"V{i:02}"
            positive = account in VAL_POSITIVES
            add_labelled_alert(
                conn, account, VAL_DAY, escalate=positive, typologies=["FAN-IN"] if positive and i < 3 else []
            )
        for i in range(9):
            account = f"T{i}"
            add_labelled_alert(conn, account, TEST_DAY, escalate=account in TEST_POSITIVES, typologies=[])
    return db


def settings_of(db: Database) -> Settings:
    return load_settings().with_database(str(conninfo_to_dict(db.owner_dsn)["dbname"]))


@pytest.fixture
def workspace(tmp_path: Path, populated: Database) -> Path:
    thresholds = tmp_path / "config" / "thresholds" / f"{settings_of(populated).database}.json"
    thresholds.parent.mkdir(parents=True)
    thresholds.write_text('{"thresholds": {}}\n')
    log = tmp_path / "docs" / "test-split-log.md"
    log.parent.mkdir()
    log.write_text(LOG_HEADER)
    return tmp_path


class ScoreByAccount:
    def __init__(self, name: str, positives: set[str]) -> None:
        self.name = name
        self.positives = positives

    async def decide(self, alert: EvalAlert) -> SystemDecision:
        return SystemDecision(None, risk_score=0.9 if alert.account_id in self.positives else 0.1)


def test_sample_is_deterministic_and_stratified(populated: Database, tmp_path: Path) -> None:
    settings = settings_of(populated)

    first = create_sample(settings, split="val", size=12, seed=7, test_reason=None, workspace=tmp_path / "a")
    second = create_sample(settings, split="val", size=12, seed=7, test_reason=None, workspace=tmp_path / "b")

    sample = load_sample(first)
    assert sample.keys == load_sample(second).keys
    assert list(sample.keys) == sorted(sample.keys)
    assert sample.population == Strata(positive=6, negative=24)
    assert sample.drawn == Strata(positive=4, negative=8)
    assert sum(1 for key in sample.keys if key.account_id in VAL_POSITIVES) == 4
    assert {key.alert_date for key in sample.keys} == {VAL_DAY}
    assert first == tmp_path / "a" / "eval" / "samples" / settings.database / "val.json"
    with pytest.raises(FileExistsError):
        create_sample(settings, split="val", size=12, seed=7, test_reason=None, workspace=tmp_path / "a")


def test_sample_takes_all_positives_when_fewer(populated: Database, tmp_path: Path) -> None:
    path = create_sample(settings_of(populated), split="val", size=30, seed=7, test_reason=None, workspace=tmp_path)

    assert load_sample(path).drawn == Strata(positive=6, negative=24)


@pytest.mark.anyio
async def test_natural_keys_resolve_and_missing_keys_fail(populated: Database) -> None:
    keys = [AlertKey("V01", VAL_DAY), AlertKey("T0", TEST_DAY)]

    alerts = await resolve_alerts(populated.app_dsn, keys)

    assert [(a.account_id, a.alert_date) for a in alerts] == [("T0", TEST_DAY), ("V01", VAL_DAY)]
    with pytest.raises(NotFoundError, match="V01@2022-09-08"):
        await resolve_alerts(populated.app_dsn, [*keys, AlertKey("V01", date(2022, 9, 8))])


HAND_EXAMPLE = [
    Outcome(True, ("FAN-IN",), "close", typologies=("FAN-IN", "CYCLE"), latency_ms=10),
    Outcome(True, (), "escalate", latency_ms=20),
    Outcome(False, (), "close", latency_ms=30),
    Outcome(False, (), "close", latency_ms=40),
    Outcome(False, (), "close", latency_ms=50),
    Outcome(False, (), "escalate", latency_ms=60),
]
HAND_POPULATION = {True: 10, False: 90}


def test_weighted_and_unweighted_metrics_on_a_hand_computed_example() -> None:
    weights = stratum_weights(HAND_POPULATION, [o.escalate_truth for o in HAND_EXAMPLE])
    assert weights == pytest.approx({True: 0.3, False: 1.35})

    weighted = measure(HAND_EXAMPLE, [weights[o.escalate_truth] for o in HAND_EXAMPLE])
    unweighted = measure(HAND_EXAMPLE, [1.0] * len(HAND_EXAMPLE))

    assert weighted["false_close_rate"] == pytest.approx(0.5)
    assert weighted["close_rate"] == pytest.approx(4.35 / 6)
    assert weighted["escalation_precision"] == pytest.approx(0.3 / 1.65)
    assert weighted["mean_latency_ms"] == pytest.approx(42)
    assert unweighted["false_close_rate"] == pytest.approx(0.5)
    assert unweighted["close_rate"] == pytest.approx(4 / 6)
    assert unweighted["escalation_precision"] == pytest.approx(0.5)
    assert unweighted["mean_latency_ms"] == pytest.approx(35)
    assert unweighted["p50_latency_ms"] == 30
    assert unweighted["p95_latency_ms"] == 60
    for metrics in (weighted, unweighted):
        assert metrics["recall_pattern"] == 0
        assert metrics["recall_other"] == 1
        assert metrics["typology_hit_rate"] == 1
        assert metrics["mean_cost_usd"] is None


def test_typology_hit_rate_is_absent_when_the_system_emits_no_typologies() -> None:
    silent = [Outcome(o.escalate_truth, o.truth_typologies, o.recommendation) for o in HAND_EXAMPLE]

    assert measure(silent, [1.0] * len(silent))["typology_hit_rate"] is None


def test_bootstrap_interval_contains_the_point_estimate_and_is_reproducible() -> None:
    outcomes = HAND_EXAMPLE * 5

    first = report(outcomes, HAND_POPULATION, resamples=500)
    second = report(outcomes, HAND_POPULATION, resamples=500)

    assert first == second
    for estimates in (first.weighted, first.unweighted):
        for name in ("false_close_rate", "close_rate", "escalation_precision", "mean_latency_ms"):
            estimate = estimates[name]
            assert estimate.value is not None
            assert estimate.low is not None
            assert estimate.high is not None
            assert estimate.low <= estimate.value <= estimate.high
            assert estimate.low < estimate.high


def test_operating_point_closes_most_within_the_false_close_limit() -> None:
    negatives = [ScoredAlert(i / 100, False) for i in range(1, 41)]
    positives = [ScoredAlert(0.15, True), ScoredAlert(0.2, True)] + [ScoredAlert(0.9, True)] * 18
    scored = negatives + positives
    weights = {True: 1.0, False: 1.0}

    point = choose_operating_point(scored, weights)

    assert point.threshold == 0.2
    assert point.false_close_rate == pytest.approx(0.05)
    assert point.close_rate == pytest.approx(20 / 60)
    assert operating_point_at(0.21, scored, weights).false_close_rate > 0.05


@pytest.mark.anyio
async def test_runner_end_to_end_on_val(populated: Database, workspace: Path) -> None:
    settings = settings_of(populated)
    sample_file = create_sample(settings, split="val", size=12, seed=7, test_reason=None, workspace=workspace)

    result = await evaluate_system(
        settings, ScoreByAccount("oracle", VAL_POSITIVES), split="val", test_reason=None, workspace=workspace
    )

    assert result.path == workspace / "eval" / "results" / settings.database / "val" / "oracle.json"
    document = json.loads(result.path.read_text())
    assert document["operating_point"]["threshold"] == 0.9
    assert document["metrics"]["weighted"]["false_close_rate"]["value"] == 0
    assert document["metrics"]["weighted"]["close_rate"]["value"] == pytest.approx(0.8)
    assert document["metrics"]["unweighted"]["close_rate"]["value"] == pytest.approx(8 / 12)
    assert document["counts"]["sample"] == {"positive": 4, "negative": 8}
    assert document["counts"]["recommended_close"] == 8
    assert len(document["alerts"]) == 12
    assert {row["system_recommendation"] for row in document["alerts"]} == {None}
    assert len(document["git_commit"]) == 40
    assert len(document["sample_sha256"]) == 64
    assert document["sample_sha256"] != document["thresholds_sha256"]
    assert "false_close_rate" in result.summary()
    assert str(result.path) in result.summary()
    assert sample_file.exists()


@pytest.mark.anyio
async def test_test_split_is_refused_without_a_reason(populated: Database, workspace: Path) -> None:
    settings = settings_of(populated)

    with pytest.raises(ClosedTestSplitError):
        create_sample(settings, split="test", size=6, seed=7, test_reason=None, workspace=workspace)
    with pytest.raises(ClosedTestSplitError):
        await evaluate_system(
            settings, ScoreByAccount("oracle", TEST_POSITIVES), split="test", test_reason=None, workspace=workspace
        )

    assert (workspace / "docs" / "test-split-log.md").read_text() == LOG_HEADER


@pytest.mark.anyio
async def test_test_split_reuses_the_val_operating_point_and_logs_each_opening(
    populated: Database, workspace: Path
) -> None:
    settings = settings_of(populated)
    system = ScoreByAccount("oracle", VAL_POSITIVES | TEST_POSITIVES)
    create_sample(settings, split="val", size=12, seed=7, test_reason=None, workspace=workspace)
    val = await evaluate_system(settings, system, split="val", test_reason=None, workspace=workspace)
    create_sample(settings, split="test", size=6, seed=7, test_reason="harness check", workspace=workspace)

    test = await evaluate_system(settings, system, split="test", test_reason="harness check", workspace=workspace)

    assert test.operating_point == val.operating_point
    lines = (workspace / "docs" / "test-split-log.md").read_text().splitlines()
    rows = [line for line in lines if "harness check" in line]
    assert len(rows) == 2
    assert "test sample drawn" in rows[0]
    assert "3 positive and 6 negative" in rows[0]
    assert "oracle evaluated on the test sample (6 alerts)" in rows[1]
    assert all(row.endswith("| None. |") and "| M3 |" in row for row in rows)
    assert lines[-1] == "Footer."


@pytest.mark.anyio
async def test_test_split_needs_the_val_operating_point(populated: Database, workspace: Path) -> None:
    settings = settings_of(populated)
    create_sample(settings, split="test", size=6, seed=7, test_reason="harness check", workspace=workspace)

    with pytest.raises(FileNotFoundError, match="validation split first"):
        await evaluate_system(
            settings, ScoreByAccount("oracle", TEST_POSITIVES), split="test", test_reason="x", workspace=workspace
        )
