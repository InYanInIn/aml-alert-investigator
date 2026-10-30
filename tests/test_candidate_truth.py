from datetime import date, datetime

import psycopg

from aml_investigator.truth.candidate_truth import CandidateTruthReport, compute_candidate_truth
from tests.conftest import Database

DAY = date(2022, 9, 3)
WINDOW = (datetime(2022, 9, 1), datetime(2022, 9, 4))


def add_accounts(conn: psycopg.Connection[tuple[object, ...]], *accounts: str) -> None:
    for account in accounts:
        conn.execute("INSERT INTO core.accounts VALUES (%s, 1, 'Bank', %s, 'Corporation')", (account, account))


def add_candidate(conn: psycopg.Connection[tuple[object, ...]], rule_id: str, account: str) -> None:
    conn.execute(
        "INSERT INTO core.rule_candidates VALUES (%s, %s, %s, %s, %s, 1, '{}')", (rule_id, account, DAY, *WINDOW)
    )


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


def run(db: Database) -> CandidateTruthReport:
    with psycopg.connect(db.owner_dsn) as conn:
        return compute_candidate_truth(conn)


def stored(db: Database) -> dict[tuple[str, str], bool]:
    with db.owner() as conn:
        rows = conn.execute("SELECT rule_id, account_id, has_laundering FROM truth.candidate_truth").fetchall()
    return {(str(rule_id), str(account_id)): bool(flag) for rule_id, account_id, flag in rows}


def test_laundering_in_the_window_on_either_side_marks_the_candidate(db: Database) -> None:
    with db.owner() as conn:
        add_accounts(conn, "S", "R", "N", "O")
        for account in ("S", "R", "N", "O"):
            add_candidate(conn, "R1_fan_in", account)
        add_transaction(conn, 1, datetime(2022, 9, 2, 12), "S", "R", laundering=True)
        add_transaction(conn, 2, datetime(2022, 9, 2, 13), "N", "O", laundering=False)

    report = run(db)

    assert stored(db) == {
        ("R1_fan_in", "S"): True,
        ("R1_fan_in", "R"): True,
        ("R1_fan_in", "N"): False,
        ("R1_fan_in", "O"): False,
    }
    assert report == CandidateTruthReport(candidates=4, with_laundering=2)
    assert str(report) == "candidates: 4, with laundering: 2"


def test_laundering_outside_the_half_open_window_does_not_count(db: Database) -> None:
    with db.owner() as conn:
        add_accounts(conn, "A", "B", "C", "D", "X")
        for account in "ABCD":
            add_candidate(conn, "R2_fan_out", account)
        add_transaction(conn, 1, datetime(2022, 8, 31, 23), "A", "X", laundering=True)
        add_transaction(conn, 2, WINDOW[1], "B", "X", laundering=True)
        add_transaction(conn, 3, WINDOW[0], "C", "X", laundering=True)

    run(db)

    assert stored(db) == {
        ("R2_fan_out", "A"): False,
        ("R2_fan_out", "B"): False,
        ("R2_fan_out", "C"): True,
        ("R2_fan_out", "D"): False,
    }


def test_each_rule_candidate_of_an_account_is_judged_on_its_own_window(db: Database) -> None:
    with db.owner() as conn:
        add_accounts(conn, "A", "X")
        add_candidate(conn, "R1_fan_in", "A")
        conn.execute(
            "INSERT INTO core.rule_candidates VALUES ('R2_fan_out', 'A', %s, %s, %s, 1, '{}')",
            (DAY, datetime(2022, 9, 3), WINDOW[1]),
        )
        add_transaction(conn, 1, datetime(2022, 9, 2), "A", "X", laundering=True)

    run(db)

    assert stored(db) == {("R1_fan_in", "A"): True, ("R2_fan_out", "A"): False}


def test_rerun_replaces_the_rows(db: Database) -> None:
    with db.owner() as conn:
        add_accounts(conn, "A", "B", "X")
        add_candidate(conn, "R1_fan_in", "A")
        add_transaction(conn, 1, datetime(2022, 9, 2), "A", "X", laundering=False)
    assert run(db) == CandidateTruthReport(candidates=1, with_laundering=0)

    with db.owner() as conn:
        conn.execute("UPDATE truth.transaction_labels SET is_laundering = true")
        add_candidate(conn, "R3_pass_through", "B")

    assert run(db) == CandidateTruthReport(candidates=2, with_laundering=1)
    assert stored(db) == {("R1_fan_in", "A"): True, ("R3_pass_through", "B"): False}


def test_no_candidates_is_an_empty_report(db: Database) -> None:
    assert run(db) == CandidateTruthReport(candidates=0, with_laundering=0)
