from datetime import date, datetime, timedelta

import psycopg

from aml_investigator.monitor import FAN_OUT, run_fan_out
from tests.conftest import Database

DAY = datetime(2022, 9, 5)


def seed(db: Database, accounts: list[str], transactions: list[tuple[int, datetime, str, str]]) -> None:
    with db.owner() as conn:
        for account in accounts:
            conn.execute("INSERT INTO core.accounts VALUES (%s, 1, 'Bank', %s, 'Corporation')", (account, account))
        for txn_id, ts, sender, receiver in transactions:
            conn.execute(
                "INSERT INTO core.transactions VALUES (%s, %s, %s, %s, 10, 'USD', 10, 'USD', 'ACH')",
                (txn_id, ts, sender, receiver),
            )


def run(db: Database, *, min_receivers: int = 3, window_days: int = 1) -> int:
    with psycopg.connect(db.owner_dsn) as conn:
        return run_fan_out(conn, min_receivers=min_receivers, window_days=window_days)


def alerts(db: Database) -> list[tuple[str, date, datetime, datetime]]:
    with db.owner() as conn:
        return conn.execute(
            "SELECT account_id, alert_date, window_start, window_end FROM core.alerts ORDER BY account_id, alert_date"
        ).fetchall()  # type: ignore[return-value]


def hits(db: Database) -> list[tuple[str, str, int, list[int]]]:
    with db.owner() as conn:
        return conn.execute(
            "SELECT a.account_id, h.rule_id, h.score::int, h.evidence_txn_ids "
            "FROM core.alert_rule_hits h JOIN core.alerts a USING (alert_id) ORDER BY a.account_id, a.alert_date"
        ).fetchall()  # type: ignore[return-value]


def fan_out_from_s(
    receivers: list[str], first_txn_id: int = 1, ts: datetime = DAY
) -> list[tuple[int, datetime, str, str]]:
    return [(first_txn_id + i, ts + timedelta(hours=i), "S", r) for i, r in enumerate(receivers)]


def test_alert_at_threshold_but_not_below(db: Database) -> None:
    seed(
        db,
        ["S", "R1", "R2", "R3", "Q", "Q1", "Q2"],
        [*fan_out_from_s(["R1", "R2", "R3"]), (10, DAY, "Q", "Q1"), (11, DAY, "Q", "Q2")],
    )

    assert run(db, min_receivers=3) == 1
    assert [(a[0], a[1]) for a in alerts(db)] == [("S", date(2022, 9, 5))]
    assert hits(db) == [("S", FAN_OUT, 3, [1, 2, 3])]


def test_repeat_payments_to_one_receiver_count_once(db: Database) -> None:
    seed(db, ["S", "R1", "R2"], fan_out_from_s(["R1", "R1", "R1", "R2"]))

    assert run(db, min_receivers=3) == 0
    assert run(db, min_receivers=2) == 1
    assert hits(db) == [("S", FAN_OUT, 2, [1, 2, 3, 4])]


def test_self_transfers_are_ignored(db: Database) -> None:
    seed(db, ["S", "R1", "R2"], fan_out_from_s(["R1", "R2", "S", "S"]))

    assert run(db, min_receivers=3) == 0
    assert run(db, min_receivers=2) == 1
    assert hits(db) == [("S", FAN_OUT, 2, [1, 2])]


def test_account_with_only_self_transfers_gets_no_alert(db: Database) -> None:
    seed(db, ["S"], fan_out_from_s(["S", "S", "S"]))

    assert run(db, min_receivers=1) == 0
    assert alerts(db) == []


def test_incoming_transactions_are_irrelevant(db: Database) -> None:
    seed(db, ["S", "R1", "R2", "R3"], [(1, DAY, "R1", "S"), (2, DAY, "R2", "S"), (3, DAY, "R3", "S")])

    assert run(db, min_receivers=2) == 0
    assert alerts(db) == []


def test_window_aggregates_across_days_with_exact_bounds(db: Database) -> None:
    seed(
        db,
        ["S", "R1", "R2", "R3", "R4"],
        [
            (1, datetime(2022, 9, 2, 23, 59, 59), "S", "R1"),
            (2, datetime(2022, 9, 3, 0, 0, 0), "S", "R2"),
            (3, datetime(2022, 9, 4, 12, 0, 0), "S", "R3"),
            (4, datetime(2022, 9, 5, 23, 59, 59), "S", "R4"),
            (5, datetime(2022, 9, 6, 0, 0, 0), "S", "R1"),
        ],
    )

    assert run(db, min_receivers=4, window_days=3) == 0
    assert run(db, min_receivers=3, window_days=3) == 3

    assert alerts(db) == [
        ("S", date(2022, 9, 4), datetime(2022, 9, 2), datetime(2022, 9, 5)),
        ("S", date(2022, 9, 5), datetime(2022, 9, 3), datetime(2022, 9, 6)),
        ("S", date(2022, 9, 6), datetime(2022, 9, 4), datetime(2022, 9, 7)),
    ]
    assert hits(db) == [("S", FAN_OUT, 3, [1, 2, 3]), ("S", FAN_OUT, 3, [2, 3, 4]), ("S", FAN_OUT, 3, [3, 4, 5])]


def test_window_end_is_exclusive_of_next_day(db: Database) -> None:
    seed(
        db,
        ["S", "R1", "R2", "R3"],
        [
            (1, datetime(2022, 9, 5, 0, 0, 0), "S", "R1"),
            (2, datetime(2022, 9, 5, 12, 0, 0), "S", "R2"),
            (3, datetime(2022, 9, 6, 0, 0, 0), "S", "R3"),
        ],
    )

    assert run(db, min_receivers=3, window_days=1) == 0
    assert run(db, min_receivers=3, window_days=2) == 1
    assert alerts(db) == [("S", date(2022, 9, 6), datetime(2022, 9, 5), datetime(2022, 9, 7))]


def test_multi_day_window_creates_alert_per_active_day(db: Database) -> None:
    seed(
        db,
        ["S", "R1", "R2", "R3"],
        [
            (1, datetime(2022, 9, 5, 10), "S", "R1"),
            (2, datetime(2022, 9, 6, 10), "S", "R2"),
            (3, datetime(2022, 9, 7, 10), "S", "R3"),
        ],
    )

    assert run(db, min_receivers=2, window_days=2) == 2
    assert [(a[1]) for a in alerts(db)] == [date(2022, 9, 6), date(2022, 9, 7)]
    assert hits(db) == [("S", FAN_OUT, 2, [1, 2]), ("S", FAN_OUT, 2, [2, 3])]


def test_evidence_ids_are_sorted_and_complete(db: Database) -> None:
    seed(
        db,
        ["S", "R1", "R2", "R3"],
        [
            (30, DAY + timedelta(hours=1), "S", "R1"),
            (10, DAY + timedelta(hours=5), "S", "R2"),
            (20, DAY + timedelta(hours=3), "S", "R3"),
            (25, DAY + timedelta(hours=4), "S", "R3"),
            (99, DAY + timedelta(hours=6), "R1", "S"),
        ],
    )

    run(db, min_receivers=3)

    assert hits(db) == [("S", FAN_OUT, 3, [10, 20, 25, 30])]


def test_second_run_creates_nothing(db: Database) -> None:
    seed(db, ["S", "R1", "R2", "R3"], fan_out_from_s(["R1", "R2", "R3"]))

    assert run(db, min_receivers=3) == 1
    assert run(db, min_receivers=3) == 0
    assert len(alerts(db)) == 1
    assert len(hits(db)) == 1


def test_existing_alert_is_reused_and_gets_the_hit(db: Database) -> None:
    seed(db, ["S", "R1", "R2", "R3"], fan_out_from_s(["R1", "R2", "R3"]))
    with db.owner() as conn:
        conn.execute(
            "INSERT INTO core.alerts (account_id, alert_date, window_start, window_end) "
            "VALUES ('S', '2022-09-05', '2022-09-05', '2022-09-06')"
        )
        conn.execute("INSERT INTO core.alert_rule_hits VALUES (1, 'R1_fan_in', 4, '{}')")

    assert run(db, min_receivers=3) == 0

    assert len(alerts(db)) == 1
    with db.owner() as conn:
        rules = conn.execute("SELECT rule_id FROM core.alert_rule_hits ORDER BY rule_id").fetchall()
    assert rules == [("R1_fan_in",), (FAN_OUT,)]
