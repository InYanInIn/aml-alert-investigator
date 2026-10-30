import json
from datetime import date, datetime, timedelta
from pathlib import Path

import psycopg
import pytest

from aml_investigator.data.splits import DayRange, day_splits
from aml_investigator.monitoring.alerts import build_alerts
from aml_investigator.monitoring.calibration import (
    CurvePoint,
    Feasibility,
    choose_thresholds,
    feasibility,
    rule_curves,
    train_split,
    write_thresholds,
)
from aml_investigator.monitoring.rules import rules
from tests.conftest import Database

BASE = date(2022, 9, 1)
TRAIN = DayRange(BASE, BASE + timedelta(days=3))
RULE_IDS = [rule.RULE_ID for rule in rules()]
FILLER = "F"


def at(day_offset: int, hour: int = 12) -> datetime:
    return datetime.combine(BASE + timedelta(days=day_offset), datetime.min.time()) + timedelta(hours=hour)


def add_accounts(db: Database, *accounts: str) -> None:
    with db.owner() as conn:
        for account in accounts:
            conn.execute("INSERT INTO core.accounts VALUES (%s, 1, 'Bank', %s, 'Corporation')", (account, account))


def add_candidate(db: Database, rule_id: str, account: str, day_offset: int, score: float) -> None:
    alert_date = BASE + timedelta(days=day_offset)
    window_start = datetime.combine(alert_date, datetime.min.time())
    with db.owner() as conn:
        conn.execute(
            "INSERT INTO core.rule_candidates VALUES (%s, %s, %s, %s, %s, %s, '{}')",
            (rule_id, account, alert_date, window_start, window_start + timedelta(days=1), score),
        )


def add_transaction(db: Database, txn_id: int, ts: datetime, sender: str, receiver: str, *, laundering: bool) -> None:
    with db.owner() as conn:
        conn.execute(
            "INSERT INTO core.transactions VALUES (%s, %s, %s, %s, 100, 'Euro', 100, 'Euro', 'ACH', 100)",
            (txn_id, ts, sender, receiver),
        )
        conn.execute("INSERT INTO truth.transaction_labels VALUES (%s, %s)", (txn_id, laundering))


def add_attempt(db: Database, attempt_id: int, txn_id: int) -> None:
    with db.owner() as conn:
        conn.execute("INSERT INTO truth.pattern_attempts VALUES (%s, 'FAN-IN', %s)", (attempt_id, txn_id))


def add_filler_candidates(db: Database, *, except_rule: str) -> None:
    add_accounts(db, FILLER)
    for rule_id in RULE_IDS:
        if rule_id != except_rule:
            add_candidate(db, rule_id, FILLER, 0, 1)


def choose(db: Database, share_per_day: float, suppression_days: int, *, dsn: str | None = None) -> dict[str, float]:
    with psycopg.connect(dsn or db.owner_dsn) as conn:
        return choose_thresholds(
            conn, TRAIN, budget_per_day=share_per_day * len(RULE_IDS), suppression_days=suppression_days
        )


def test_picks_the_lowest_score_whose_volume_fits_the_share(db: Database) -> None:
    add_accounts(db, "A", "B", "C", "D")
    add_filler_candidates(db, except_rule="R1_fan_in")
    for account, score in zip("ABCD", (1, 2, 3, 4), strict=True):
        add_candidate(db, "R1_fan_in", account, 0, score)

    thresholds = choose(db, share_per_day=0.5, suppression_days=0)

    assert thresholds["R1_fan_in"] == 3
    assert {thresholds[rule_id] for rule_id in RULE_IDS if rule_id != "R1_fan_in"} == {1}


def test_suppressed_hits_do_not_count_against_the_share(db: Database) -> None:
    add_accounts(db, "A", "B")
    add_filler_candidates(db, except_rule="R1_fan_in")
    for offset in range(4):
        add_candidate(db, "R1_fan_in", "A", offset, 5)
    add_candidate(db, "R1_fan_in", "B", 0, 2)
    add_candidate(db, "R1_fan_in", "B", 2, 2)

    assert choose(db, share_per_day=0.5, suppression_days=3)["R1_fan_in"] == 2


def test_candidate_before_the_train_window_still_suppresses(db: Database) -> None:
    add_accounts(db, "A", "B")
    add_filler_candidates(db, except_rule="R1_fan_in")
    add_candidate(db, "R1_fan_in", "A", -1, 5)
    add_candidate(db, "R1_fan_in", "A", 0, 5)
    add_candidate(db, "R1_fan_in", "B", 0, 1)

    assert choose(db, share_per_day=0.25, suppression_days=1)["R1_fan_in"] == 1


def test_rule_too_loud_even_at_its_top_score_is_switched_off(db: Database) -> None:
    add_accounts(db, "A")
    add_filler_candidates(db, except_rule="R1_fan_in")
    for offset in range(4):
        add_candidate(db, "R1_fan_in", "A", offset, 5)

    with pytest.warns(UserWarning, match="R1_fan_in.*switched off"):
        thresholds = choose(db, share_per_day=0.5, suppression_days=0)

    assert thresholds["R1_fan_in"] > 5
    assert thresholds["R2_fan_out"] == 1


def test_chosen_volume_matches_build_alerts_when_lowering_the_threshold_suppresses_more(db: Database) -> None:
    add_accounts(db, "A")
    add_filler_candidates(db, except_rule="R1_fan_in")
    add_candidate(db, "R1_fan_in", "A", 1, 5)
    add_candidate(db, "R1_fan_in", "A", 2, 1)
    add_candidate(db, "R1_fan_in", "A", 3, 5)

    thresholds = choose(db, share_per_day=0.25, suppression_days=1)
    with psycopg.connect(db.owner_dsn) as conn:
        build_alerts(conn, thresholds, suppression_days=1)
        hits = conn.execute("SELECT count(*) FROM core.alert_rule_hits WHERE rule_id = 'R1_fan_in'").fetchone()

    assert thresholds["R1_fan_in"] == 1
    assert hits == (1,)


def test_rule_without_train_candidates_is_an_error(db: Database) -> None:
    add_filler_candidates(db, except_rule="R1_fan_in")

    with pytest.raises(ValueError, match="R1_fan_in"):
        choose(db, share_per_day=1, suppression_days=0)


def test_threshold_choice_runs_as_the_app_role_that_cannot_read_labels(db: Database) -> None:
    add_accounts(db, "A", "B")
    add_filler_candidates(db, except_rule="R1_fan_in")
    add_candidate(db, "R1_fan_in", "A", 0, 1)
    add_candidate(db, "R1_fan_in", "B", 0, 2)
    add_transaction(db, 1, at(0), "A", "B", laundering=True)
    add_transaction(db, 2, at(9), "A", "B", laundering=False)

    with psycopg.connect(db.app_dsn) as conn, pytest.raises(psycopg.errors.InsufficientPrivilege):
        conn.execute("SELECT FROM truth.transaction_labels")
    with psycopg.connect(db.app_dsn) as conn:
        assert train_split(conn) == day_splits(BASE, BASE + timedelta(days=9))

    assert choose(db, share_per_day=0.25, suppression_days=0, dsn=db.app_dsn)["R1_fan_in"] == 2


def test_rule_curves_report_volume_positives_and_attempts(db: Database) -> None:
    add_accounts(db, "A", "B", "X")
    add_filler_candidates(db, except_rule="R1_fan_in")
    add_candidate(db, "R1_fan_in", "A", 0, 2)
    add_candidate(db, "R1_fan_in", "B", 0, 1)
    add_transaction(db, 1, at(0), "X", "A", laundering=True)
    add_transaction(db, 2, at(2), "B", "X", laundering=True)
    add_transaction(db, 3, at(1), "X", "X", laundering=True)
    add_transaction(db, 4, at(6), "X", "A", laundering=True)
    add_attempt(db, 7, 1)
    add_attempt(db, 8, 3)
    add_attempt(db, 9, 4)

    with psycopg.connect(db.evaluator_dsn) as conn:
        points = [point for point in rule_curves(conn, TRAIN, suppression_days=0) if point.rule_id == "R1_fan_in"]

    assert [point.quantile for point in points] == [0.5, 0.75, 0.9, 0.95, 0.98, 0.99, 0.995, 0.999]
    assert points[0] == CurvePoint("R1_fan_in", 0.5, 1.0, 4, 2, 1, 1, 2)
    assert set(points[1:]) == {CurvePoint("R1_fan_in", point.quantile, 2.0, 4, 1, 1, 1, 2) for point in points[1:]}
    assert points[0].alerts_per_day == 0.5
    assert points[0].positive_share == 0.5


def test_feasibility_counts_merged_alerts_on_validation_days_only(db: Database) -> None:
    add_accounts(db, "A", "B", "C", "X")
    val = DayRange(BASE + timedelta(days=4), BASE + timedelta(days=5))
    add_candidate(db, "R1_fan_in", "A", 4, 5)
    add_candidate(db, "R2_fan_out", "A", 4, 5)
    add_candidate(db, "R1_fan_in", "B", 5, 5)
    add_candidate(db, "R1_fan_in", "C", 3, 5)
    add_candidate(db, "R1_fan_in", "C", 4, 5)
    add_candidate(db, "R1_fan_in", "C", 6, 5)
    add_transaction(db, 1, at(4), "A", "X", laundering=True)
    add_transaction(db, 2, at(6), "X", "B", laundering=True)

    with psycopg.connect(db.evaluator_dsn) as conn:
        result = feasibility(conn, val, {"R1_fan_in": 3, "R2_fan_out": 3}, suppression_days=1)

    assert result == Feasibility(days=2, alerts=2, positive_alerts=1)
    assert result.alerts_per_day == 1
    assert result.positive_per_day == 0.5
    assert result.projected_test_positives == 1


def test_written_thresholds_are_what_run_alerts_reads(tmp_path: Path) -> None:
    path = tmp_path / "config" / "aml_hi_small.json"

    write_thresholds(path, {"R1_fan_in": 3.5}, budget_per_day=60, suppression_days=7, train=TRAIN)

    assert json.loads(path.read_text()) == {
        "budget_per_day": 60,
        "suppression_days": 7,
        "calibrated_on": {"first": "2022-09-01", "last": "2022-09-04"},
        "thresholds": {"R1_fan_in": 3.5},
    }
