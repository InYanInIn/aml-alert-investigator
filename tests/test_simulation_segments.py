import warnings
from datetime import date
from pathlib import Path
from typing import Any

import psycopg
import pytest

from aml_investigator.data.splits import DayRange
from aml_investigator.monitoring.alerts import build_alerts_for_days
from aml_investigator.monitoring.rules import rules
from aml_investigator.simulation.segments import (
    SEGMENTS,
    apply_segment_thresholds,
    budget_shares,
    calibrate_segments,
    load_segments,
    segments_path,
    write_segments,
)
from aml_investigator.simulation.world import MONITORED_BANK_ID
from tests.conftest import Database

DAY = date(2022, 9, 5)
RULE = "R1_fan_in"
OTHER_RULE = "R3_pass_through"
ACCOUNTS = (
    ("I1", MONITORED_BANK_ID, "Individual"),
    ("I2", MONITORED_BANK_ID, "Individual"),
    ("S1", MONITORED_BANK_ID, "Sole Proprietorship"),
    ("S2", MONITORED_BANK_ID, "Partnership"),
    ("C1", MONITORED_BANK_ID, "Corporation"),
    ("X1", 99, "Corporation"),
    ("X2", 99, "Individual"),
)


def seed_accounts(db: Database) -> None:
    with db.owner() as conn:
        for account_id, bank_id, entity_type in ACCOUNTS:
            conn.execute(
                "INSERT INTO core.accounts VALUES (%s, %s, 'bank', %s, %s)",
                (account_id, bank_id, f"E-{account_id}", entity_type),
            )


def add_candidate(
    conn: psycopg.Connection[Any], rule_id: str, account_id: str, score: float, alert_date: date = DAY
) -> None:
    conn.execute(
        "INSERT INTO core.rule_candidates VALUES (%s, %s, %s, %s, %s, %s, %s)",
        (rule_id, account_id, alert_date, "2022-09-01", "2022-09-05", score, [1]),
    )


def all_rules_at_zero() -> dict[str, float]:
    return {rule.RULE_ID: 0.0 for rule in rules()}


def alerted_accounts(conn: psycopg.Connection[Any]) -> set[str]:
    ids = build_alerts_for_days(conn, all_rules_at_zero(), DayRange(DAY, DAY), suppression_days=3)
    return {row[0] for row in conn.execute("SELECT account_id FROM core.alerts WHERE alert_id = ANY(%s)", (ids,))}


def test_budget_is_split_by_monitored_accounts_and_external_accounts_do_not_count(world_db: Database) -> None:
    seed_accounts(world_db)
    with world_db.owner() as conn:
        shares = budget_shares(conn, 2.0)

    assert shares == pytest.approx({"individual": 0.8, "small_business": 0.8, "corporation": 0.4})
    assert sum(shares.values()) == pytest.approx(2.0)


def test_an_individual_above_its_threshold_alerts_and_a_corporation_with_the_same_score_does_not(
    world_db: Database,
) -> None:
    seed_accounts(world_db)
    thresholds = {"individual": {RULE: 5.0}, "small_business": {RULE: 5.0}, "corporation": {RULE: 9.0}}
    with world_db.owner() as conn:
        add_candidate(conn, RULE, "I1", 6.0)
        add_candidate(conn, RULE, "C1", 6.0)
        apply_segment_thresholds(conn, thresholds)

        assert alerted_accounts(conn) == {"I1"}


def test_a_rule_without_a_threshold_in_a_segment_yields_no_alert_there(world_db: Database) -> None:
    seed_accounts(world_db)
    thresholds = {"individual": {RULE: 1.0}, "small_business": {}, "corporation": {RULE: 1.0, OTHER_RULE: 1.0}}
    with world_db.owner() as conn:
        add_candidate(conn, RULE, "S1", 50.0)
        add_candidate(conn, OTHER_RULE, "I1", 50.0)
        add_candidate(conn, RULE, "C1", 50.0)
        apply_segment_thresholds(conn, thresholds)

        assert alerted_accounts(conn) == {"C1"}


def test_calibration_leaves_the_candidates_unchanged(world_db: Database) -> None:
    seed_accounts(world_db)
    with world_db.owner() as conn:
        for index, account in enumerate(("I1", "I2", "S1", "S2", "C1")):
            add_candidate(conn, RULE, account, 1.0 + index)
        before = conn.execute("SELECT * FROM core.rule_candidates ORDER BY account_id").fetchall()

        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            calibrate_segments(conn, DayRange(DAY, DAY), budget_per_day=2.0, suppression_days=3)

        assert conn.execute("SELECT * FROM core.rule_candidates ORDER BY account_id").fetchall() == before


def test_segments_file_round_trips(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    thresholds = {segment: {RULE: 1.5} for segment in SEGMENTS}

    path = write_segments("aml_sim_dev", thresholds, budget_per_day=2.0, suppression_days=3, train=DayRange(DAY, DAY))

    assert path == segments_path("aml_sim_dev")
    assert load_segments("aml_sim_dev").thresholds == thresholds
    assert load_segments("aml_sim_dev").suppression_days == 3


def test_a_missing_segments_file_names_the_database(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)

    with pytest.raises(FileNotFoundError, match="aml_sim_dev"):
        load_segments("aml_sim_dev")
