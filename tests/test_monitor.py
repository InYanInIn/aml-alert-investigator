from datetime import date, datetime, timedelta
from types import ModuleType

import psycopg
import pytest

from aml_investigator.monitoring.rules import r1_fan_in, r2_fan_out, r5_structuring
from tests.conftest import Database

DAY = datetime(2022, 9, 5)

Transaction = tuple[int, datetime, str, str, float]
Candidate = tuple[str, date, datetime, datetime, int, list[int]]


def seed(db: Database, accounts: list[str], transactions: list[Transaction]) -> None:
    with db.owner() as conn:
        for account in accounts:
            conn.execute("INSERT INTO core.accounts VALUES (%s, 1, 'Bank', %s, 'Corporation')", (account, account))
        for txn_id, ts, sender, receiver, amount_eur in transactions:
            conn.execute(
                "INSERT INTO core.transactions VALUES (%s, %s, %s, %s, 1, 'USD', 1, 'USD', 'ACH', %s)",
                (txn_id, ts, sender, receiver, amount_eur),
            )


def generate(db: Database, rule: ModuleType) -> int:
    with psycopg.connect(db.owner_dsn) as conn:
        return int(rule.generate(conn))


def candidates(db: Database, rule: ModuleType) -> list[Candidate]:
    with db.owner() as conn:
        return conn.execute(  # type: ignore[return-value]
            "SELECT account_id, alert_date, window_start, window_end, score::int, evidence_txn_ids "
            "FROM core.rule_candidates WHERE rule_id = %s ORDER BY account_id, alert_date",
            (rule.RULE_ID,),
        ).fetchall()


def midnight(day: int, hour: int = 0, minute: int = 0, second: int = 0) -> datetime:
    return datetime(2022, 9, day, hour, minute, second)


@pytest.fixture(params=[r1_fan_in, r2_fan_out], ids=lambda rule: rule.RULE_ID)
def fan_rule(request: pytest.FixtureRequest) -> ModuleType:
    return request.param  # type: ignore[no-any-return]


def fan_txn(rule: ModuleType, txn_id: int, ts: datetime, counterparty: str, subject: str = "S") -> Transaction:
    sender, receiver = (counterparty, subject) if rule is r1_fan_in else (subject, counterparty)
    return (txn_id, ts, sender, receiver, 10)


def opposite_fan_txn(rule: ModuleType, txn_id: int, ts: datetime, counterparty: str) -> Transaction:
    other = r2_fan_out if rule is r1_fan_in else r1_fan_in
    return fan_txn(other, txn_id, ts, counterparty)


def test_fan_rule_constants(fan_rule: ModuleType) -> None:
    assert (fan_rule.WINDOW_DAYS, fan_rule.FLOOR) == (2, 2)


def test_fan_candidate_at_floor_but_not_below(db: Database, fan_rule: ModuleType) -> None:
    seed(
        db,
        ["S", "C1", "C2", "Q", "Q1"],
        [
            fan_txn(fan_rule, 1, DAY, "C1"),
            fan_txn(fan_rule, 2, DAY + timedelta(hours=1), "C2"),
            fan_txn(fan_rule, 10, DAY, "Q1", subject="Q"),
        ],
    )

    assert generate(db, fan_rule) == 1
    assert candidates(db, fan_rule) == [("S", date(2022, 9, 5), midnight(4), midnight(6), 2, [1, 2])]


def test_fan_repeat_counterparty_counts_once(db: Database, fan_rule: ModuleType) -> None:
    seed(
        db,
        ["S", "C1", "C2"],
        [fan_txn(fan_rule, i + 1, DAY + timedelta(hours=i), c) for i, c in enumerate(["C1", "C1", "C1"])],
    )
    assert generate(db, fan_rule) == 0

    seed(db, [], [fan_txn(fan_rule, 4, DAY + timedelta(hours=3), "C2")])

    assert generate(db, fan_rule) == 1
    assert candidates(db, fan_rule) == [("S", date(2022, 9, 5), midnight(4), midnight(6), 2, [1, 2, 3, 4])]


def test_fan_self_transfers_are_ignored(db: Database, fan_rule: ModuleType) -> None:
    seed(
        db,
        ["S", "C1", "C2"],
        [
            fan_txn(fan_rule, 1, DAY, "C1"),
            fan_txn(fan_rule, 2, DAY, "S"),
            fan_txn(fan_rule, 3, DAY, "S"),
        ],
    )
    assert generate(db, fan_rule) == 0

    seed(db, [], [fan_txn(fan_rule, 4, DAY, "C2")])

    assert generate(db, fan_rule) == 1
    assert candidates(db, fan_rule) == [("S", date(2022, 9, 5), midnight(4), midnight(6), 2, [1, 4])]


def test_fan_account_with_only_self_transfers_has_no_candidate(db: Database, fan_rule: ModuleType) -> None:
    seed(db, ["S"], [fan_txn(fan_rule, i, DAY, "S") for i in (1, 2, 3)])

    assert generate(db, fan_rule) == 0
    assert candidates(db, fan_rule) == []


def test_fan_opposite_direction_is_irrelevant(db: Database, fan_rule: ModuleType) -> None:
    seed(
        db,
        ["S", "C1", "C2", "C3"],
        [opposite_fan_txn(fan_rule, i, DAY, c) for i, c in enumerate(["C1", "C2", "C3"], start=1)],
    )

    assert generate(db, fan_rule) == 0
    assert candidates(db, fan_rule) == []


def test_fan_window_bounds_are_exact(db: Database, fan_rule: ModuleType) -> None:
    seed(
        db,
        ["S", "C1", "C2", "C3", "C4"],
        [
            fan_txn(fan_rule, 1, midnight(4, 23, 59, 59), "C1"),
            fan_txn(fan_rule, 2, midnight(5), "C2"),
            fan_txn(fan_rule, 3, midnight(6, 23, 59, 59), "C3"),
            fan_txn(fan_rule, 4, midnight(7), "C4"),
        ],
    )

    assert generate(db, fan_rule) == 3
    assert candidates(db, fan_rule) == [
        ("S", date(2022, 9, 5), midnight(4), midnight(6), 2, [1, 2]),
        ("S", date(2022, 9, 6), midnight(5), midnight(7), 2, [2, 3]),
        ("S", date(2022, 9, 7), midnight(6), midnight(8), 2, [3, 4]),
    ]


def test_fan_evidence_is_sorted_and_complete(db: Database, fan_rule: ModuleType) -> None:
    seed(
        db,
        ["S", "C1", "C2", "C3"],
        [
            fan_txn(fan_rule, 30, DAY + timedelta(hours=1), "C1"),
            fan_txn(fan_rule, 10, DAY + timedelta(hours=5), "C2"),
            fan_txn(fan_rule, 20, DAY + timedelta(hours=3), "C3"),
            fan_txn(fan_rule, 25, DAY + timedelta(hours=4), "C3"),
            fan_txn(fan_rule, 40, DAY + timedelta(hours=4), "S"),
            opposite_fan_txn(fan_rule, 99, DAY + timedelta(hours=6), "C1"),
        ],
    )

    generate(db, fan_rule)

    assert candidates(db, fan_rule) == [("S", date(2022, 9, 5), midnight(4), midnight(6), 3, [10, 20, 25, 30])]


def test_fan_rerun_replaces_instead_of_duplicating(db: Database, fan_rule: ModuleType) -> None:
    seed(db, ["S", "C1", "C2"], [fan_txn(fan_rule, 1, DAY, "C1"), fan_txn(fan_rule, 2, DAY, "C2")])
    with db.owner() as conn:
        conn.execute(
            "INSERT INTO core.rule_candidates VALUES (%s, 'S', '2022-01-01', '2022-01-01', '2022-01-02', 9, '{}')",
            (fan_rule.RULE_ID,),
        )
        conn.execute(
            "INSERT INTO core.rule_candidates VALUES ('other_rule', 'S', '2022-01-01', "
            "'2022-01-01', '2022-01-02', 9, '{}')"
        )

    assert generate(db, fan_rule) == 1
    assert generate(db, fan_rule) == 1

    assert candidates(db, fan_rule) == [("S", date(2022, 9, 5), midnight(4), midnight(6), 2, [1, 2])]
    with db.owner() as conn:
        assert conn.execute("SELECT count(*) FROM core.rule_candidates WHERE rule_id = 'other_rule'").fetchone() == (1,)


def test_fan_rules_do_not_see_each_others_candidates(db: Database) -> None:
    seed(
        db,
        ["S", "C1", "C2"],
        [fan_txn(r2_fan_out, 1, DAY, "C1"), fan_txn(r2_fan_out, 2, DAY, "C2")],
    )

    assert generate(db, r2_fan_out) == 1
    assert generate(db, r1_fan_in) == 0
    assert len(candidates(db, r2_fan_out)) == 1


def structuring_txn(
    txn_id: int, ts: datetime, amount_eur: float, receiver: str = "R", sender: str = "S"
) -> Transaction:
    return (txn_id, ts, sender, receiver, amount_eur)


def test_structuring_constants() -> None:
    assert r5_structuring.RULE_ID == "R5_structuring"
    assert (r5_structuring.WINDOW_DAYS, r5_structuring.FLOOR) == (3, 2)
    assert r5_structuring.REPORTING_THRESHOLD_EUR == 15000
    assert r5_structuring.BAND_LOWER == 0.85


def test_structuring_band_boundaries(db: Database) -> None:
    seed(
        db,
        ["S", "R"],
        [
            structuring_txn(1, DAY, 12749.99),
            structuring_txn(2, DAY + timedelta(hours=1), 12750),
            structuring_txn(3, DAY + timedelta(hours=2), 14999.99),
            structuring_txn(4, DAY + timedelta(hours=3), 15000),
        ],
    )

    assert generate(db, r5_structuring) == 1
    assert candidates(db, r5_structuring) == [("S", date(2022, 9, 5), midnight(3), midnight(6), 2, [2, 3])]


@pytest.mark.parametrize("amount_eur", [12749.99, 15000])
def test_structuring_amounts_outside_band_never_count(db: Database, amount_eur: float) -> None:
    seed(db, ["S", "R"], [structuring_txn(1, DAY, amount_eur), structuring_txn(2, DAY, amount_eur)])

    assert generate(db, r5_structuring) == 0


def test_structuring_candidate_at_floor_but_not_below(db: Database) -> None:
    seed(db, ["S", "R", "Q"], [structuring_txn(1, DAY, 14000), structuring_txn(2, DAY, 14000, sender="Q")])
    assert generate(db, r5_structuring) == 0

    seed(db, [], [structuring_txn(3, DAY + timedelta(hours=1), 14000)])

    assert generate(db, r5_structuring) == 1
    assert candidates(db, r5_structuring) == [("S", date(2022, 9, 5), midnight(3), midnight(6), 2, [1, 3])]


def test_structuring_repeat_receiver_counts_every_payment(db: Database) -> None:
    seed(
        db,
        ["S", "R"],
        [structuring_txn(i, DAY + timedelta(hours=i), 14000) for i in (1, 2, 3)],
    )

    generate(db, r5_structuring)

    assert candidates(db, r5_structuring) == [("S", date(2022, 9, 5), midnight(3), midnight(6), 3, [1, 2, 3])]


def test_structuring_uses_eur_amount_not_paid_amount(db: Database) -> None:
    seed(db, ["S", "R"], [structuring_txn(1, DAY, 14000), structuring_txn(2, DAY, 14000)])
    with db.owner() as conn:
        conn.execute("UPDATE core.transactions SET amount_paid = 20000, paid_currency = 'PLN'")

    assert generate(db, r5_structuring) == 1


def test_structuring_self_transfers_are_ignored(db: Database) -> None:
    seed(
        db,
        ["S", "R"],
        [structuring_txn(1, DAY, 14000), structuring_txn(2, DAY, 14000, receiver="S")],
    )

    assert generate(db, r5_structuring) == 0


def test_structuring_incoming_payments_are_irrelevant(db: Database) -> None:
    seed(
        db,
        ["S", "R"],
        [
            structuring_txn(1, DAY, 14000, sender="R", receiver="S"),
            structuring_txn(2, DAY, 14000, sender="R", receiver="S"),
        ],
    )

    assert generate(db, r5_structuring) == 1
    assert [c[0] for c in candidates(db, r5_structuring)] == ["R"]


def test_structuring_window_bounds_are_exact(db: Database) -> None:
    seed(
        db,
        ["S", "R"],
        [
            structuring_txn(1, midnight(4, 23, 59, 59), 14000),
            structuring_txn(2, midnight(5), 14000),
            structuring_txn(3, midnight(7, 23, 59, 59), 14000),
            structuring_txn(4, midnight(8), 14000),
        ],
    )

    assert generate(db, r5_structuring) == 3
    assert candidates(db, r5_structuring) == [
        ("S", date(2022, 9, 5), midnight(3), midnight(6), 2, [1, 2]),
        ("S", date(2022, 9, 7), midnight(5), midnight(8), 2, [2, 3]),
        ("S", date(2022, 9, 8), midnight(6), midnight(9), 2, [3, 4]),
    ]


def test_structuring_active_day_requires_an_in_band_payment(db: Database) -> None:
    seed(
        db,
        ["S", "R"],
        [
            structuring_txn(1, midnight(5, 1), 14000),
            structuring_txn(2, midnight(5, 2), 14000),
            structuring_txn(3, midnight(6), 500),
        ],
    )

    assert generate(db, r5_structuring) == 1
    assert [c[1] for c in candidates(db, r5_structuring)] == [date(2022, 9, 5)]


def test_structuring_evidence_is_sorted_and_only_in_band(db: Database) -> None:
    seed(
        db,
        ["S", "R"],
        [
            structuring_txn(30, DAY + timedelta(hours=1), 14000),
            structuring_txn(10, DAY + timedelta(hours=5), 13000),
            structuring_txn(20, DAY + timedelta(hours=3), 14999),
            structuring_txn(25, DAY + timedelta(hours=4), 100),
        ],
    )

    generate(db, r5_structuring)

    assert candidates(db, r5_structuring) == [("S", date(2022, 9, 5), midnight(3), midnight(6), 3, [10, 20, 30])]


def test_structuring_rerun_replaces_instead_of_duplicating(db: Database) -> None:
    seed(db, ["S", "R"], [structuring_txn(1, DAY, 14000), structuring_txn(2, DAY, 14000)])
    with db.owner() as conn:
        conn.execute(
            "INSERT INTO core.rule_candidates VALUES ('R5_structuring', 'S', '2022-01-01', "
            "'2022-01-01', '2022-01-02', 9, '{}')"
        )

    assert generate(db, r5_structuring) == 1
    assert generate(db, r5_structuring) == 1

    assert candidates(db, r5_structuring) == [("S", date(2022, 9, 5), midnight(3), midnight(6), 2, [1, 2])]
