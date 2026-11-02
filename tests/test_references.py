import re
from collections import Counter
from datetime import datetime
from pathlib import Path

import psycopg

from aml_investigator.data.references import fill_references
from tests.conftest import Database
from tests.test_ingest import run_ingest, write_raw

ROWS = 20_000
LAUNDERING_EVERY = 4
MONTHS = "January|February|March|April|May|June|July|August|September|October|November|December"
TEMPLATE_COUNT = 20
MAX_SHARE_DIFFERENCE = 0.02
FIRST_REFERENCE = "Invoice INV-23786"

Conn = psycopg.Connection[tuple[object, ...]]


def load_transactions(conn: Conn) -> None:
    conn.execute(
        "INSERT INTO core.accounts VALUES "
        "('A1', 1, 'Bank', 'E1', 'Corporation'), ('A2', 2, 'Bank', 'E2', 'Corporation')"
    )
    start = datetime(2022, 9, 1)
    conn.execute(
        "INSERT INTO core.transactions (txn_id, ts, from_account, to_account, amount_paid, paid_currency, "
        "amount_received, received_currency, payment_format, amount_paid_eur) "
        "SELECT i, %(start)s + i * interval '1 second', 'A1', 'A2', 100, 'Euro', 100, 'Euro', 'ACH', 100 "
        "FROM generate_series(1, %(rows)s) AS i",
        {"start": start, "rows": ROWS},
    )
    conn.execute(
        "INSERT INTO truth.transaction_labels SELECT i, i %% %(every)s = 0 FROM generate_series(1, %(rows)s) AS i",
        {"every": LAUNDERING_EVERY, "rows": ROWS},
    )


def references(db: Database) -> dict[int, str]:
    with db.owner() as conn:
        return {
            int(str(row[0])): str(row[1]) for row in conn.execute("SELECT txn_id, reference FROM core.transactions")
        }


def template_of(reference: str) -> str:
    return re.sub(r"\b[0-9A-F]{6}\b", "{code}", re.sub(r"\b\d{5}\b", "{n}", re.sub(MONTHS, "{month}", reference)))


def test_fill_sets_every_null_reference_and_reports_the_count(db: Database) -> None:
    with db.owner() as conn:
        load_transactions(conn)
        assert fill_references(conn) == ROWS

    filled = references(db)
    assert len(filled) == ROWS
    assert filled[1] == FIRST_REFERENCE
    assert len({template_of(reference) for reference in filled.values()}) == TEMPLATE_COUNT


def test_fill_is_idempotent_and_keeps_existing_references(db: Database) -> None:
    with db.owner() as conn:
        load_transactions(conn)
        fill_references(conn)
        first = references(db)
        conn.execute("UPDATE core.transactions SET reference = 'custom' WHERE txn_id = 7")
        conn.execute("UPDATE core.transactions SET reference = NULL WHERE txn_id = 8")

        assert fill_references(conn) == 1
        assert fill_references(conn) == 0

    second = references(db)
    assert second[7] == "custom"
    assert second[8] == first[8]
    assert {txn_id: ref for txn_id, ref in second.items() if txn_id != 7} == {
        txn_id: ref for txn_id, ref in first.items() if txn_id != 7
    }


def test_reference_does_not_depend_on_label_amount_format_or_accounts(db: Database) -> None:
    with db.owner() as conn:
        load_transactions(conn)
        fill_references(conn)
        before = references(db)
        conn.execute("UPDATE truth.transaction_labels SET is_laundering = NOT is_laundering")
        conn.execute("INSERT INTO core.accounts VALUES ('A3', 3, 'Bank', 'E3', 'Individual')")
        conn.execute(
            "UPDATE core.transactions SET amount_paid = amount_paid * 977, amount_paid_eur = amount_paid_eur * 977, "
            "payment_format = 'Wire', from_account = 'A2', to_account = 'A3', ts = ts + interval '3 days', "
            "reference = NULL"
        )
        assert fill_references(conn) == ROWS

    assert references(db) == before


def test_templates_are_independent_of_the_laundering_label(db: Database) -> None:
    with db.owner() as conn:
        load_transactions(conn)
        fill_references(conn)
        rows = conn.execute(
            "SELECT t.reference, l.is_laundering "
            "FROM core.transactions t JOIN truth.transaction_labels l USING (txn_id)"
        ).fetchall()

    by_label: dict[bool, Counter[str]] = {True: Counter(), False: Counter()}
    for reference, laundering in rows:
        by_label[bool(laundering)][template_of(str(reference))] += 1
    totals = {label: sum(counts.values()) for label, counts in by_label.items()}
    shares = {
        label: {t: counts[t] / totals[label] for t in by_label[True] | by_label[False]}
        for label, counts in by_label.items()
    }
    differences = [abs(shares[True][t] - shares[False][t]) for t in shares[True]]

    assert totals[True] == ROWS // LAUNDERING_EVERY
    assert max(differences) < MAX_SHARE_DIFFERENCE


def test_ingest_fills_references(db: Database, tmp_path: Path) -> None:
    run_ingest(db, write_raw(tmp_path))

    filled = references(db)
    assert len(filled) == 7
    assert all(template_of(reference) != "" for reference in filled.values())
