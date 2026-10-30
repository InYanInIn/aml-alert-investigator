from collections.abc import Mapping
from datetime import date, datetime, timedelta
from typing import Any

import psycopg
import pytest

from aml_investigator.monitoring.alerts import build_alerts
from tests.conftest import Database

BASE = date(2022, 9, 5)


def add_accounts(db: Database, *accounts: str) -> None:
    with db.owner() as conn:
        for account in accounts:
            conn.execute("INSERT INTO core.accounts VALUES (%s, 1, 'Bank', %s, 'Corporation')", (account, account))


def add_candidate(
    db: Database,
    rule_id: str,
    account: str,
    day_offset: int,
    score: float,
    *,
    evidence: list[int] | None = None,
    window_days: int = 1,
) -> None:
    alert_date = BASE + timedelta(days=day_offset)
    window_end = datetime.combine(alert_date + timedelta(days=1), datetime.min.time())
    with db.owner() as conn:
        conn.execute(
            "INSERT INTO core.rule_candidates VALUES (%s, %s, %s, %s, %s, %s, %s)",
            (rule_id, account, alert_date, window_end - timedelta(days=window_days), window_end, score, evidence or []),
        )


def build(db: Database, thresholds: Mapping[str, float], suppression_days: int = 0) -> int:
    with psycopg.connect(db.owner_dsn) as conn:
        return build_alerts(conn, thresholds, suppression_days=suppression_days)


def rows(db: Database, query: str, *params: object) -> list[tuple[Any, ...]]:
    with db.owner() as conn:
        return conn.execute(query, params).fetchall()


def alert_days(db: Database, account: str = "A") -> list[date]:
    query = "SELECT alert_date FROM core.alerts WHERE account_id = %s ORDER BY alert_date"
    return [row[0] for row in rows(db, query, account)]


def test_threshold_boundary(db: Database) -> None:
    add_accounts(db, "A", "B", "C")
    add_candidate(db, "R2_fan_out", "A", 0, 2.9)
    add_candidate(db, "R2_fan_out", "B", 0, 3)
    add_candidate(db, "R2_fan_out", "C", 0, 3.1)

    assert build(db, {"R2_fan_out": 3}) == 2
    assert rows(db, "SELECT account_id FROM core.alerts ORDER BY account_id") == [("B",), ("C",)]


def test_rule_without_threshold_produces_no_alerts(db: Database) -> None:
    add_accounts(db, "A", "B")
    add_candidate(db, "R2_fan_out", "A", 0, 5)
    add_candidate(db, "R1_fan_in", "B", 0, 5)

    assert build(db, {"R2_fan_out": 1}) == 1
    assert rows(db, "SELECT account_id FROM core.alerts") == [("A",)]


def test_no_thresholds_produce_no_alerts(db: Database) -> None:
    add_accounts(db, "A")
    add_candidate(db, "R2_fan_out", "A", 0, 5)

    assert build(db, {}) == 0


@pytest.mark.parametrize(
    ("suppression_days", "gap_days", "alerts_expected"),
    [(0, 1, 2), (1, 1, 1), (1, 2, 2), (2, 2, 1), (2, 3, 2), (3, 3, 1)],
)
def test_suppression_reaches_exactly_suppression_days_back(
    db: Database, suppression_days: int, gap_days: int, alerts_expected: int
) -> None:
    add_accounts(db, "A")
    add_candidate(db, "R2_fan_out", "A", 0, 5)
    add_candidate(db, "R2_fan_out", "A", gap_days, 5)

    assert build(db, {"R2_fan_out": 3}, suppression_days) == alerts_expected
    assert alert_days(db) == [BASE, BASE + timedelta(days=gap_days)][:alerts_expected]


def test_multi_day_episode_alerts_once_on_its_first_day(db: Database) -> None:
    add_accounts(db, "A")
    for offset in range(4):
        add_candidate(db, "R2_fan_out", "A", offset, 5)

    assert build(db, {"R2_fan_out": 3}, suppression_days=1) == 1
    assert alert_days(db) == [BASE]


def test_non_qualifying_candidate_does_not_suppress(db: Database) -> None:
    add_accounts(db, "A")
    add_candidate(db, "R2_fan_out", "A", 0, 2)
    add_candidate(db, "R2_fan_out", "A", 1, 5)

    assert build(db, {"R2_fan_out": 3}, suppression_days=1) == 1
    assert alert_days(db) == [BASE + timedelta(days=1)]


def test_suppression_is_per_rule_and_per_account(db: Database) -> None:
    add_accounts(db, "A", "B")
    add_candidate(db, "R2_fan_out", "A", 0, 5)
    add_candidate(db, "R1_fan_in", "A", 1, 5)
    add_candidate(db, "R2_fan_out", "B", 1, 5)

    thresholds = {"R1_fan_in": 3, "R2_fan_out": 3}
    assert build(db, thresholds, suppression_days=1) == 3
    assert alert_days(db, "A") == [BASE, BASE + timedelta(days=1)]
    assert alert_days(db, "B") == [BASE + timedelta(days=1)]


def test_two_rules_on_same_account_day_merge_into_one_alert(db: Database) -> None:
    add_accounts(db, "A")
    add_candidate(db, "R2_fan_out", "A", 0, 4, evidence=[1, 2, 3], window_days=2)
    add_candidate(db, "R5_structuring", "A", 0, 6, evidence=[2, 5], window_days=3)

    assert build(db, {"R2_fan_out": 3, "R5_structuring": 3}) == 1

    assert rows(db, "SELECT account_id, alert_date, window_start, window_end FROM core.alerts") == [
        ("A", BASE, datetime(2022, 9, 3), datetime(2022, 9, 6))
    ]
    assert rows(db, "SELECT rule_id, score::int, evidence_txn_ids FROM core.alert_rule_hits ORDER BY rule_id") == [
        ("R2_fan_out", 4, [1, 2, 3]),
        ("R5_structuring", 6, [2, 5]),
    ]


def test_only_qualifying_rule_becomes_a_hit(db: Database) -> None:
    add_accounts(db, "A")
    add_candidate(db, "R2_fan_out", "A", 0, 4, evidence=[1])
    add_candidate(db, "R1_fan_in", "A", 0, 1, evidence=[2])

    assert build(db, {"R2_fan_out": 3, "R1_fan_in": 3}) == 1
    assert rows(db, "SELECT rule_id FROM core.alert_rule_hits") == [("R2_fan_out",)]


def test_unknown_rule_key_raises_and_creates_nothing(db: Database) -> None:
    add_accounts(db, "A")
    add_candidate(db, "R2_fan_out", "A", 0, 5)

    with pytest.raises(ValueError, match="R9_imaginary"):
        build(db, {"R2_fan_out": 1, "R9_imaginary": 1})

    assert rows(db, "SELECT count(*) FROM core.alerts") == [(0,)]


def test_refuses_when_alerts_already_exist(db: Database) -> None:
    add_accounts(db, "A")
    add_candidate(db, "R2_fan_out", "A", 0, 5)
    with db.owner() as conn:
        conn.execute(
            "INSERT INTO core.alerts (account_id, alert_date, window_start, window_end) "
            "VALUES ('A', '2022-01-01', '2022-01-01', '2022-01-02')"
        )

    with pytest.raises(RuntimeError, match="not empty"):
        build(db, {"R2_fan_out": 1})

    assert rows(db, "SELECT alert_date FROM core.alerts") == [(date(2022, 1, 1),)]
    assert rows(db, "SELECT count(*) FROM core.alert_rule_hits") == [(0,)]
