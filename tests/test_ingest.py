import hashlib
from datetime import date
from decimal import Decimal
from pathlib import Path

import psycopg
import pytest

from aml_investigator.data.ingest import SALT, IngestReport, ingest
from tests.conftest import Database

PREFIX = "TEST"
PERIOD_END = date(2022, 9, 10)
TRANS_HEADER = (
    "Timestamp,From Bank,Account,To Bank,Account,Amount Received,Receiving Currency,"
    "Amount Paid,Payment Currency,Payment Format,Is Laundering"
)
SELF_TRANSFER = "2022/09/01 00:20,010,8000EBD30,010,8000EBD30,3697.34,US Dollar,3697.34,US Dollar,Reinvestment,0"
CHEQUE = "2022/09/01 00:20,010,8000EBD30,001,8000F5340,0.01,US Dollar,0.01,US Dollar,Cheque,0"
CARD = "2022/09/01 08:00,001,8000F5340,010,8000EBD30,12.50,US Dollar,12.50,US Dollar,Credit Card,0"
FAN_OUT_LEG = "2022/09/02 10:00,001,8000F5340,0220,80020C5B0,500.00,Euro,500.00,Euro,ACH,1"
GATHER_LEG = "2022/09/03 11:00,0220,80020C5B0,010,8000EBD30,490.00,Euro,490.00,Euro,ACH,1"
TAIL_LEG = "2022/09/11 09:00,0220,80020C5B0,001,8000F5340,480.00,Euro,480.00,Euro,ACH,1"
DOLLARS_TO_EUROS = "2022/09/04 09:00,001,8000F5340,010,8000EBD30,50.00,Euro,100.00,US Dollar,Wire,0"
TRANSACTIONS = [SELF_TRANSFER, CHEQUE, CHEQUE, CARD, FAN_OUT_LEG, GATHER_LEG, TAIL_LEG, DOLLARS_TO_EUROS]
ACCOUNTS = [
    "Bank Name,Bank ID,Account Number,Entity ID,Entity Name",
    "First Bank #1,10,8000EBD30,80062E240,Corporation #33520",
    "Second Bank #2,1,8000F5340,800C998A0,Sole Proprietorship #50438",
    "Third Bank #3,220,80020C5B0,800C998A0,Sole Proprietorship #50438",
    "Unused Bank #4,331579,80B779D80,80062E241,Individual #7",
]
PATTERNS = [
    "BEGIN LAUNDERING ATTEMPT - FAN-OUT:  Max 16-degree Fan-Out",
    FAN_OUT_LEG,
    "END LAUNDERING ATTEMPT - FAN-OUT",
    "",
    "BEGIN LAUNDERING ATTEMPT - SCATTER-GATHER",
    GATHER_LEG,
    TAIL_LEG,
    "END LAUNDERING ATTEMPT - SCATTER-GATHER",
]
SOURCE_IDENTIFIERS = ["8000EBD30", "8000F5340", "80020C5B0", "80062E240", "800C998A0", "33520", "50438"]
TRANSACTION_CONTENT = (
    "SELECT t.txn_id, t.ts, f.bank_id, t.from_account, r.bank_id, t.to_account, t.amount_paid, t.payment_format "
    "FROM core.transactions t JOIN core.accounts f ON f.account_id = t.from_account "
    "JOIN core.accounts r ON r.account_id = t.to_account ORDER BY t.txn_id"
)


def cross_currency(received: str, received_currency: str, paid: str, paid_currency: str) -> str:
    return f"2022/09/05 12:00,010,8000EBD30,001,8000F5340,{received},{received_currency},{paid},{paid_currency},Wire,0"


RATE_FIXTURE = [
    cross_currency("50", "Euro", "100", "US Dollar"),
    cross_currency("60", "Euro", "100", "US Dollar"),
    cross_currency("5", "Euro", "10", "US Dollar"),
    cross_currency("200", "US Dollar", "100", "Euro"),
    cross_currency("1.25", "US Dollar", "10", "Yuan"),
    cross_currency("10", "Euro", "10", "Swiss Franc"),
    cross_currency("25", "Swiss Franc", "100", "US Dollar"),
    cross_currency("5", "US Dollar", "1", "Swiss Franc"),
]


def write_raw(raw_dir: Path, transactions: list[str] = TRANSACTIONS) -> Path:
    raw_dir.mkdir(exist_ok=True)
    (raw_dir / f"{PREFIX}_Trans.csv").write_text("\n".join([TRANS_HEADER, *transactions]) + "\n")
    (raw_dir / f"{PREFIX}_accounts.csv").write_text("\n".join(ACCOUNTS) + "\n")
    (raw_dir / f"{PREFIX}_Patterns.txt").write_text("\n".join(PATTERNS) + "\n")
    return raw_dir


def run_ingest(db: Database, raw_dir: Path, limit: int | None = None) -> IngestReport:
    with psycopg.connect(db.owner_dsn) as conn:
        return ingest(conn, raw_dir, PREFIX, period_end=PERIOD_END, limit=limit)


def fetch(db: Database, query: str) -> list[tuple[object, ...]]:
    with db.owner() as conn:
        return conn.execute(query).fetchall()


def clear(db: Database) -> None:
    with db.owner() as conn:
        conn.execute(
            "TRUNCATE truth.pattern_attempts, truth.transaction_labels, core.transactions, core.fx_rates, "
            "core.accounts CASCADE"
        )


def expected_account_id(bank_id: int, account_number: str) -> str:
    return "A" + hashlib.sha256(f"{SALT}|{bank_id}|{account_number}".encode()).hexdigest()[:12]


def test_loads_in_period_rows_and_drops_tail(db: Database, tmp_path: Path) -> None:
    report = run_ingest(db, write_raw(tmp_path))

    assert report == IngestReport(
        transactions=7,
        accounts=3,
        laundering=2,
        pattern_rows=2,
        dropped_tail=1,
        currencies=2,
        fx_round_trip_error=0.0,
    )
    assert fetch(db, "SELECT max(ts) < '2022-09-11' FROM core.transactions") == [(True,)]
    assert "transactions: 7" in str(report)


def test_leading_zero_bank_ids_join_to_accounts(db: Database, tmp_path: Path) -> None:
    run_ingest(db, write_raw(tmp_path))

    assert fetch(db, "SELECT bank_id, bank_name FROM core.accounts ORDER BY bank_id") == [
        (1, "Second Bank #2"),
        (10, "First Bank #1"),
        (220, "Third Bank #3"),
    ]
    banks = {(row[2], row[4]) for row in fetch(db, TRANSACTION_CONTENT)}
    assert banks == {(10, 10), (10, 1), (1, 10), (1, 220), (220, 10)}


def test_exact_duplicate_rows_get_distinct_ids(db: Database, tmp_path: Path) -> None:
    run_ingest(db, write_raw(tmp_path))

    cheques = fetch(db, "SELECT txn_id FROM core.transactions WHERE payment_format = 'Cheque'")
    assert len(cheques) == 2
    assert len(set(cheques)) == 2


def test_ids_do_not_depend_on_file_order(db: Database, tmp_path: Path) -> None:
    run_ingest(db, write_raw(tmp_path / "ordered"))
    ordered = fetch(db, TRANSACTION_CONTENT)
    clear(db)
    shuffled = [TRANSACTIONS[position] for position in (5, 2, 7, 6, 0, 4, 1, 3)]
    run_ingest(db, write_raw(tmp_path / "shuffled", shuffled))

    assert fetch(db, TRANSACTION_CONTENT) == ordered
    assert [row[0] for row in ordered] == [1, 2, 3, 4, 5, 6, 7]


def test_limit_keeps_the_same_ids(db: Database, tmp_path: Path) -> None:
    raw_dir = write_raw(tmp_path)
    run_ingest(db, raw_dir)
    full = fetch(db, TRANSACTION_CONTENT)
    clear(db)

    report = run_ingest(db, raw_dir, limit=4)

    assert fetch(db, TRANSACTION_CONTENT) == full[:4]
    assert report.transactions == 4
    assert report.pattern_rows == 0


def test_pseudonyms_are_deterministic_and_hide_source_identifiers(db: Database, tmp_path: Path) -> None:
    run_ingest(db, write_raw(tmp_path))

    accounts = fetch(db, "SELECT account_id, bank_id, entity_id, entity_type FROM core.accounts ORDER BY bank_id")
    assert [row[0] for row in accounts] == [
        expected_account_id(1, "8000F5340"),
        expected_account_id(10, "8000EBD30"),
        expected_account_id(220, "80020C5B0"),
    ]
    second, first, third = accounts
    assert second[2] == third[2] != first[2]
    assert str(first[2]).startswith("E")
    assert len(str(first[2])) == 13
    assert [row[3] for row in accounts] == ["Sole Proprietorship", "Corporation", "Sole Proprietorship"]
    dump = str(fetch(db, "SELECT * FROM core.accounts")) + str(fetch(db, "SELECT * FROM core.transactions"))
    assert not [identifier for identifier in SOURCE_IDENTIFIERS if identifier in dump]


def test_pattern_rows_map_to_loaded_txn_ids(db: Database, tmp_path: Path) -> None:
    raw_dir = write_raw(tmp_path)
    run_ingest(db, raw_dir)

    assert fetch(db, "SELECT attempt_id, typology, txn_id FROM truth.pattern_attempts ORDER BY attempt_id") == [
        (1, "fan_out", 5),
        (2, "scatter_gather", 6),
    ]
    clear(db)

    run_ingest(db, raw_dir, limit=5)
    assert fetch(db, "SELECT attempt_id, typology, txn_id FROM truth.pattern_attempts") == [(1, "fan_out", 5)]


def test_labels_land_only_in_truth(db: Database, tmp_path: Path) -> None:
    run_ingest(db, write_raw(tmp_path))

    assert fetch(db, "SELECT txn_id FROM truth.transaction_labels WHERE is_laundering ORDER BY txn_id") == [
        (5,),
        (6,),
    ]
    assert fetch(db, "SELECT count(*) FROM truth.transaction_labels") == [(7,)]
    label_columns = fetch(
        db,
        "SELECT table_schema, table_name FROM information_schema.columns "
        "WHERE column_name LIKE '%launder%' AND table_schema <> 'truth'",
    )
    assert label_columns == []


def test_refuses_to_load_into_non_empty_database(db: Database, tmp_path: Path) -> None:
    raw_dir = write_raw(tmp_path)
    run_ingest(db, raw_dir)

    with pytest.raises(RuntimeError, match="not empty"):
        run_ingest(db, raw_dir)
    assert fetch(db, "SELECT count(*) FROM core.transactions") == [(7,)]
    assert fetch(db, "SELECT count(*) FROM core.accounts") == [(3,)]


def test_fx_rates_come_from_median_cross_currency_ratios(db: Database, tmp_path: Path) -> None:
    report = run_ingest(db, write_raw(tmp_path, TRANSACTIONS + RATE_FIXTURE))

    rates = fetch(db, "SELECT currency, eur_per_unit::float FROM core.fx_rates ORDER BY currency")
    assert rates == [("Euro", 1.0), ("Swiss Franc", 1.0), ("US Dollar", 0.5), ("Yuan", 0.0625)]
    assert report.currencies == 4
    assert report.fx_round_trip_error == pytest.approx(0.25)


def test_unreachable_currency_raises(db: Database, tmp_path: Path) -> None:
    yen = "2022/09/04 10:00,001,8000F5340,010,8000EBD30,1000,Yen,1000,Yen,Cash,0"

    with pytest.raises(ValueError, match="Yen"):
        run_ingest(db, write_raw(tmp_path, [*TRANSACTIONS, yen]))
    assert fetch(db, "SELECT count(*) FROM core.transactions") == [(0,)]


def test_amount_paid_eur_converts_the_paid_currency(db: Database, tmp_path: Path) -> None:
    run_ingest(db, write_raw(tmp_path))

    assert fetch(
        db,
        "SELECT count(*) FROM core.transactions t JOIN core.fx_rates f ON f.currency = t.paid_currency "
        "WHERE t.amount_paid_eur <> t.amount_paid * f.eur_per_unit",
    ) == [(0,)]
    assert fetch(db, "SELECT paid_currency, amount_paid, amount_paid_eur FROM core.transactions ORDER BY txn_id") == [
        ("US Dollar", Decimal("0.01"), Decimal("0.005")),
        ("US Dollar", Decimal("0.01"), Decimal("0.005")),
        ("US Dollar", Decimal("3697.34"), Decimal("1848.67")),
        ("US Dollar", Decimal("12.50"), Decimal("6.25")),
        ("Euro", Decimal("500.00"), Decimal("500.0")),
        ("Euro", Decimal("490.00"), Decimal("490.0")),
        ("US Dollar", Decimal("100.00"), Decimal("50.0")),
    ]
