from datetime import datetime

import psycopg
import pytest

from aml_investigator.truth import TruthReport, compute_alert_truth
from tests.conftest import Database

ACCOUNTS = ["A1", "A2", "A3", "A4", "A5"]
WINDOW = (datetime(2022, 9, 3), datetime(2022, 9, 5))


def add_accounts(conn: psycopg.Connection[tuple[object, ...]]) -> None:
    for account in ACCOUNTS:
        conn.execute("INSERT INTO core.accounts VALUES (%s, 1, 'Bank', %s, 'Corporation')", (account, account))


def add_transaction(
    conn: psycopg.Connection[tuple[object, ...]],
    txn_id: int,
    ts: datetime,
    sender: str,
    receiver: str,
    *,
    laundering: bool,
) -> None:
    conn.execute(
        "INSERT INTO core.transactions VALUES (%s, %s, %s, %s, 100, 'Euro', 100, 'Euro', 'ACH', 100)",
        (txn_id, ts, sender, receiver),
    )
    conn.execute("INSERT INTO truth.transaction_labels VALUES (%s, %s)", (txn_id, laundering))


def add_attempt(conn: psycopg.Connection[tuple[object, ...]], attempt_id: int, typology: str, txn_id: int) -> None:
    conn.execute("INSERT INTO truth.pattern_attempts VALUES (%s, %s, %s)", (attempt_id, typology, txn_id))


def add_alert(conn: psycopg.Connection[tuple[object, ...]], account: str) -> int:
    row = conn.execute(
        "INSERT INTO core.alerts (account_id, alert_date, window_start, window_end) "
        "VALUES (%s, %s, %s, %s) RETURNING alert_id",
        (account, WINDOW[1].date(), *WINDOW),
    ).fetchone()
    assert row is not None
    return int(str(row[0]))


def run_truth(db: Database) -> TruthReport:
    with psycopg.connect(db.owner_dsn) as conn:
        return compute_alert_truth(conn)


def truth_of(db: Database, alert_id: int) -> tuple[object, ...]:
    with db.owner() as conn:
        row = conn.execute(
            "SELECT escalate_truth, escalate_truth_account_level, typologies FROM truth.alert_truth "
            "WHERE alert_id = %s",
            (alert_id,),
        ).fetchone()
    assert row is not None
    return row


def test_laundering_inside_the_window_escalates(db: Database) -> None:
    with db.owner() as conn:
        add_accounts(conn)
        add_transaction(conn, 1, datetime(2022, 9, 3), "A1", "A2", laundering=True)
        add_transaction(conn, 2, datetime(2022, 9, 4), "A3", "A4", laundering=False)
        receiver_alert = add_alert(conn, "A2")
        clean_alert = add_alert(conn, "A3")

    report = run_truth(db)

    assert report == TruthReport(alerts=2, escalate_true=1, account_level_true=1, with_typology=0)
    assert truth_of(db, receiver_alert) == (True, True, [])
    assert truth_of(db, clean_alert) == (False, False, [])
    assert "escalate true: 1" in str(report)


def test_laundering_outside_the_window_is_account_level_only(db: Database) -> None:
    with db.owner() as conn:
        add_accounts(conn)
        add_transaction(conn, 1, datetime(2022, 9, 2, 23, 59), "A1", "A2", laundering=True)
        add_transaction(conn, 2, WINDOW[1], "A3", "A1", laundering=True)
        add_attempt(conn, 1, "cycle", 1)
        alert = add_alert(conn, "A1")

    report = run_truth(db)

    assert truth_of(db, alert) == (False, True, [])
    assert report == TruthReport(alerts=1, escalate_true=0, account_level_true=1, with_typology=0)


def test_typologies_come_from_attempts_of_in_window_laundering(db: Database) -> None:
    with db.owner() as conn:
        add_accounts(conn)
        add_transaction(conn, 1, datetime(2022, 9, 3, 1), "A1", "A2", laundering=True)
        add_transaction(conn, 2, datetime(2022, 9, 4, 1), "A2", "A3", laundering=True)
        add_transaction(conn, 3, datetime(2022, 9, 4, 2), "A2", "A4", laundering=True)
        add_transaction(conn, 4, datetime(2022, 9, 6), "A2", "A4", laundering=True)
        add_attempt(conn, 1, "scatter_gather", 1)
        add_attempt(conn, 2, "fan_out", 2)
        add_attempt(conn, 3, "fan_out", 3)
        add_attempt(conn, 4, "cycle", 4)
        two_typologies = add_alert(conn, "A2")
        one_typology = add_alert(conn, "A3")
        sender_side = add_alert(conn, "A1")
        add_transaction(conn, 5, datetime(2022, 9, 3, 5), "A5", "A1", laundering=True)
        no_pattern = add_alert(conn, "A5")

    report = run_truth(db)

    assert truth_of(db, two_typologies) == (True, True, ["fan_out", "scatter_gather"])
    assert truth_of(db, one_typology) == (True, True, ["fan_out"])
    assert truth_of(db, sender_side) == (True, True, ["scatter_gather"])
    assert truth_of(db, no_pattern) == (True, True, [])
    assert report == TruthReport(alerts=4, escalate_true=4, account_level_true=4, with_typology=3)


def test_refuses_when_alert_truth_is_not_empty(db: Database) -> None:
    with db.owner() as conn:
        add_accounts(conn)
        add_alert(conn, "A1")
    run_truth(db)

    with pytest.raises(RuntimeError, match="not empty"):
        run_truth(db)
    with db.owner() as conn:
        assert conn.execute("SELECT count(*) FROM truth.alert_truth").fetchone() == (1,)
