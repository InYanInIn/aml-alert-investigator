import math
from datetime import date, datetime, timedelta
from decimal import Decimal
from types import ModuleType

import psycopg
import pytest

from aml_investigator.monitor import r3_pass_through, r4_cycle, r6_profile_deviation
from tests.conftest import Database

D = datetime(2022, 9, 5)
Txn = tuple[int, datetime, str, str, str | int]
Candidate = tuple[str, date, datetime, datetime, Decimal, list[int]]


def at(day_offset: int, hour: int = 0, minute: int = 0) -> datetime:
    return D + timedelta(days=day_offset, hours=hour, minutes=minute)


def midnight(day_offset: int) -> datetime:
    return at(day_offset)


def seed(db: Database, transactions: list[Txn]) -> None:
    accounts = sorted({account for _, _, sender, receiver, _ in transactions for account in (sender, receiver)})
    with db.owner() as conn:
        for account in accounts:
            conn.execute("INSERT INTO core.accounts VALUES (%s, 1, 'Bank', %s, 'Corporation')", (account, account))
        for txn_id, ts, sender, receiver, eur in transactions:
            conn.execute(
                "INSERT INTO core.transactions (txn_id, ts, from_account, to_account, amount_paid, paid_currency, "
                "amount_received, received_currency, payment_format, amount_paid_eur) "
                "VALUES (%s, %s, %s, %s, %s, 'Euro', %s, 'Euro', 'ACH', %s)",
                (txn_id, ts, sender, receiver, Decimal(eur), Decimal(eur), Decimal(eur)),
            )


def run(db: Database, rule: ModuleType) -> int:
    with psycopg.connect(db.owner_dsn) as conn:
        written: int = rule.generate(conn)
        return written


def candidates(db: Database, rule: ModuleType) -> list[Candidate]:
    with db.owner() as conn:
        return conn.execute(
            "SELECT account_id, alert_date, window_start, window_end, score, evidence_txn_ids "
            "FROM core.rule_candidates WHERE rule_id = %s ORDER BY account_id, alert_date",
            (rule.RULE_ID,),
        ).fetchall()  # type: ignore[return-value]


def scores(db: Database, rule: ModuleType) -> dict[str, Decimal]:
    return {account: score for account, _, _, _, score, _ in candidates(db, rule)}


def evidence(db: Database, rule: ModuleType) -> dict[str, list[int]]:
    return {account: txn_ids for account, _, _, _, _, txn_ids in candidates(db, rule)}


def ring(first_txn_id: int, accounts: list[str], start: datetime, amounts: list[str | int]) -> list[Txn]:
    hops = zip(accounts, [*accounts[1:], accounts[0]], amounts, strict=True)
    return [
        (first_txn_id + i, start + timedelta(hours=i), sender, receiver, eur)
        for i, (sender, receiver, eur) in enumerate(hops)
    ]


@pytest.mark.parametrize("rule", [r3_pass_through, r4_cycle, r6_profile_deviation])
def test_empty_database_writes_no_candidates(db: Database, rule: ModuleType) -> None:
    assert run(db, rule) == 0


class TestPassThrough:
    def test_passed_volume_is_the_score(self, db: Database) -> None:
        seed(db, [(1, at(-1, 10), "X", "A", 2000), (2, at(0, 9), "A", "Y", 1800)])

        assert run(db, r3_pass_through) == 1
        assert candidates(db, r3_pass_through) == [("A", D.date(), midnight(-1), midnight(1), Decimal(1800), [1, 2])]

    @pytest.mark.parametrize(
        ("out_eur", "qualifies"),
        [("7999.99", False), ("8000", True), ("12000", True), ("12000.01", False)],
    )
    def test_ratio_bounds_are_inclusive(self, db: Database, out_eur: str, qualifies: bool) -> None:
        seed(db, [(1, at(0, 1), "X", "A", 10_000), (2, at(0, 2), "A", "Y", out_eur)])

        run(db, r3_pass_through)

        assert scores(db, r3_pass_through) == ({"A": min(Decimal(out_eur), Decimal(10_000))} if qualifies else {})

    @pytest.mark.parametrize(("eur", "qualifies"), [("999.99", False), ("1000", True)])
    def test_floor_on_passed_volume(self, db: Database, eur: str, qualifies: bool) -> None:
        seed(db, [(1, at(0, 1), "X", "A", eur), (2, at(0, 2), "A", "Y", eur)])

        run(db, r3_pass_through)

        assert scores(db, r3_pass_through) == ({"A": Decimal(eur)} if qualifies else {})

    def test_window_covers_previous_day_from_midnight(self, db: Database) -> None:
        seed(
            db,
            [
                (1, at(-2, 23, 59), "X", "A", 1000),
                (2, at(0, 12), "A", "Y", 1000),
                (3, at(-1), "X", "B", 1000),
                (4, at(0, 23, 59), "B", "Y", 1000),
                (5, at(1), "Z", "B", 5000),
            ],
        )

        run(db, r3_pass_through)

        assert candidates(db, r3_pass_through) == [("B", D.date(), midnight(-1), midnight(1), Decimal(1000), [3, 4])]

    def test_needs_outgoing_on_the_alert_day(self, db: Database) -> None:
        seed(db, [(1, at(0, 1), "A", "Y", 1000), (2, at(1, 1), "X", "A", 1000)])

        assert run(db, r3_pass_through) == 0

    def test_self_transfers_are_ignored(self, db: Database) -> None:
        seed(
            db,
            [
                (1, at(0, 1), "X", "A", 1000),
                (2, at(0, 2), "A", "A", 5000),
                (3, at(0, 3), "A", "Y", 1000),
                (4, at(0, 4), "S", "S", 5000),
            ],
        )

        run(db, r3_pass_through)

        assert evidence(db, r3_pass_through) == {"A": [1, 3]}

    def test_rerun_replaces_candidates(self, db: Database) -> None:
        seed(db, [(1, at(0, 1), "X", "A", 1000), (2, at(0, 2), "A", "Y", 1000)])

        assert run(db, r3_pass_through) == 1
        first = candidates(db, r3_pass_through)
        assert run(db, r3_pass_through) == 1
        assert candidates(db, r3_pass_through) == first


class TestCycle:
    @pytest.mark.parametrize("hops", [2, 3, 4])
    def test_cycles_up_to_four_hops_are_found(self, db: Database, hops: int) -> None:
        accounts = ["A", "B", "C", "D"][:hops]
        seed(db, ring(1, accounts, at(0, 1), [500] * hops))

        assert run(db, r4_cycle) == 1
        assert candidates(db, r4_cycle) == [
            ("A", D.date(), midnight(0), midnight(1), Decimal(500), list(range(1, hops + 1)))
        ]

    def test_five_hop_cycle_is_not_found(self, db: Database) -> None:
        seed(db, ring(1, ["A", "B", "C", "D", "E"], at(0, 1), [500] * 5))

        assert run(db, r4_cycle) == 0

    @pytest.mark.parametrize("closing_minute", [0, -1])
    def test_timestamps_must_strictly_increase(self, db: Database, closing_minute: int) -> None:
        seed(
            db,
            [
                (1, at(0, 10), "A", "B", 500),
                (2, at(0, 11), "B", "C", 500),
                (3, at(0, 11, closing_minute), "C", "A", 500),
            ],
        )

        assert run(db, r4_cycle) == 0

    def test_repeated_intermediate_is_rejected(self, db: Database) -> None:
        seed(
            db,
            [
                (1, at(0, 1), "A", "B", 1000),
                (2, at(0, 2), "B", "C", 1000),
                (3, at(0, 3), "C", "B", 1000),
                (4, at(0, 4), "B", "A", 1000),
            ],
        )

        run(db, r4_cycle)

        assert evidence(db, r4_cycle) == {"A": [1, 4], "B": [2, 3]}

    def test_span_of_exactly_four_days_is_allowed(self, db: Database) -> None:
        seed(
            db,
            [
                (1, at(-4), "A", "B", 500),
                (2, at(0), "B", "A", 500),
                (3, at(-4), "P", "Q", 500),
                (4, at(0, 0, 1), "Q", "P", 500),
            ],
        )

        assert run(db, r4_cycle) == 1
        assert candidates(db, r4_cycle) == [("A", D.date(), midnight(-4), midnight(1), Decimal(500), [1, 2])]

    def test_span_is_checked_across_both_halves_of_a_four_hop_cycle(self, db: Database) -> None:
        seed(
            db,
            [
                (1, at(-4), "A", "B", 500),
                (2, at(-3), "B", "C", 500),
                (3, at(-1), "C", "D", 500),
                (4, at(0, 0, 1), "D", "A", 500),
            ],
        )

        assert run(db, r4_cycle) == 0

    def test_window_starts_on_the_day_of_the_first_hop(self, db: Database) -> None:
        seed(db, [(1, at(-2, 23), "A", "B", 500), (2, at(0, 1), "B", "A", 500)])

        run(db, r4_cycle)

        assert candidates(db, r4_cycle) == [("A", D.date(), midnight(-2), midnight(1), Decimal(500), [1, 2])]

    @pytest.mark.parametrize(("weakest_eur", "found"), [("99.99", False), ("100", True)])
    def test_every_hop_needs_the_minimum_amount(self, db: Database, weakest_eur: str, found: bool) -> None:
        seed(db, ring(1, ["A", "B", "C"], at(0, 1), [500, weakest_eur, 500]))

        run(db, r4_cycle)

        assert scores(db, r4_cycle) == ({"A": Decimal(weakest_eur)} if found else {})

    def test_score_is_the_weakest_hop_of_the_best_cycle(self, db: Database) -> None:
        seed(
            db,
            [
                *ring(1, ["A", "B", "C", "D"], at(0, 1), [500, 300, 700, 400]),
                *ring(11, ["A", "E", "F"], at(0, 1), [900, 200, 900]),
            ],
        )

        assert run(db, r4_cycle) == 1
        assert candidates(db, r4_cycle) == [("A", D.date(), midnight(0), midnight(1), Decimal(300), [1, 2, 3, 4])]

    def test_tied_best_cycles_share_the_evidence(self, db: Database) -> None:
        seed(db, [*ring(1, ["A", "B"], at(0, 1), [300, 300]), *ring(11, ["A", "C", "D"], at(0, 1), [300, 400, 500])])

        run(db, r4_cycle)

        assert evidence(db, r4_cycle) == {"A": [1, 2, 11, 12, 13]}

    def test_evidence_is_capped_at_whole_cycles(self, db: Database) -> None:
        transactions: list[Txn] = [
            txn
            for i in range(30)
            for txn in [(2 * i + 1, at(0, 1), "A", f"B{i:02}", 100), (2 * i + 2, at(0, 2), f"B{i:02}", "A", 100)]
        ]
        seed(db, transactions)

        run(db, r4_cycle)

        assert evidence(db, r4_cycle) == {"A": list(range(1, 51))}

    def test_cycles_are_grouped_by_the_day_they_close(self, db: Database) -> None:
        seed(db, [*ring(1, ["A", "B"], at(0, 1), [500, 500]), *ring(11, ["A", "C"], at(1, 1), [700, 700])])

        run(db, r4_cycle)

        assert [(day, score) for _, day, _, _, score, _ in candidates(db, r4_cycle)] == [
            (D.date(), Decimal(500)),
            (D.date() + timedelta(days=1), Decimal(700)),
        ]

    def test_self_transfers_are_ignored(self, db: Database) -> None:
        seed(
            db,
            [
                (1, at(0, 1), "A", "B", 500),
                (2, at(0, 2), "B", "B", 900),
                (3, at(0, 3), "B", "A", 500),
                (4, at(0, 4), "S", "S", 900),
            ],
        )

        run(db, r4_cycle)

        assert evidence(db, r4_cycle) == {"A": [1, 3]}

    def test_score_is_stored_in_whole_cents(self, db: Database) -> None:
        seed(db, ring(1, ["A", "B"], at(0, 1), ["102.24000000000001", "102.2449"]))

        run(db, r4_cycle)

        assert [str(score) for score in scores(db, r4_cycle).values()] == ["102.24"]

    def test_rerun_replaces_candidates(self, db: Database) -> None:
        seed(db, ring(1, ["A", "B", "C"], at(0, 1), [500, 500, 500]))

        assert run(db, r4_cycle) == 1
        first = candidates(db, r4_cycle)
        assert run(db, r4_cycle) == 1
        assert candidates(db, r4_cycle) == first


def daily_payments(first_txn_id: int, sender: str, amounts_by_day: dict[int, str | int]) -> list[Txn]:
    return [
        (first_txn_id + i, at(day_offset, 12), sender, "R", eur)
        for i, (day_offset, eur) in enumerate(amounts_by_day.items())
    ]


def approx_scores(db: Database, rule: ModuleType) -> dict[str, float]:
    return {account: float(score) for account, score in scores(db, rule).items()}


class TestProfileDeviation:
    def test_log_z_score_against_previous_active_days(self, db: Database) -> None:
        seed(
            db,
            [
                *daily_payments(1, "A", {-9: 1000, -5: 2000, -1: 4000}),
                (4, at(0, 1), "A", "R", 10_000),
                (5, at(0, 2), "A", "S", 6000),
            ],
        )

        assert run(db, r6_profile_deviation) == 1
        assert [(row[:4], row[5]) for row in candidates(db, r6_profile_deviation)] == [
            (("A", D.date(), midnight(0), midnight(1)), [4, 5])
        ]
        assert approx_scores(db, r6_profile_deviation) == {"A": pytest.approx(3)}

    @pytest.mark.parametrize(("day_out", "flagged"), [("1648.72", False), ("1648.73", True)])
    def test_log_z_boundary_at_floor(self, db: Database, day_out: str, flagged: bool) -> None:
        seed(db, daily_payments(1, "A", {-3: 1000, -2: 1000, -1: 1000, 0: day_out}))

        run(db, r6_profile_deviation)

        assert approx_scores(db, r6_profile_deviation) == ({"A": pytest.approx(2, rel=1e-4)} if flagged else {})

    def test_log_stddev_floor_applies_to_slightly_varying_profiles(self, db: Database) -> None:
        seed(db, daily_payments(1, "A", {-3: 1000, -2: 1100, -1: 1210, 0: "2328.70"}))

        run(db, r6_profile_deviation)

        assert approx_scores(db, r6_profile_deviation) == {"A": pytest.approx(3, rel=1e-4)}

    @pytest.mark.parametrize(("day_out", "flagged"), [("999.99", False), ("1000", True)])
    def test_day_total_needs_the_minimum_amount(self, db: Database, day_out: str, flagged: bool) -> None:
        seed(db, daily_payments(1, "A", {-3: 100, -2: 100, -1: 100, 0: day_out}))

        run(db, r6_profile_deviation)

        assert approx_scores(db, r6_profile_deviation) == ({"A": pytest.approx(4 * math.log(10))} if flagged else {})

    def test_insufficient_history_is_skipped(self, db: Database) -> None:
        seed(db, daily_payments(1, "A", {-2: 1000, -1: 1000, 0: 100_000}))

        assert run(db, r6_profile_deviation) == 0

    def test_days_without_outgoing_value_are_not_active_days(self, db: Database) -> None:
        seed(db, daily_payments(1, "A", {-4: 0, -2: 1000, -1: 1000, 0: 100_000}))

        assert run(db, r6_profile_deviation) == 0

    def test_incoming_and_self_transfers_are_ignored(self, db: Database) -> None:
        seed(
            db,
            [
                *daily_payments(1, "A", {-3: 1000, -2: 1000, -1: 1000, 0: 2000}),
                (5, at(-4, 1), "A", "A", 100_000),
                (6, at(0, 1), "A", "A", 100_000),
                (7, at(0, 2), "X", "A", 100_000),
            ],
        )

        run(db, r6_profile_deviation)

        assert evidence(db, r6_profile_deviation) == {"A": [4]}
        assert approx_scores(db, r6_profile_deviation) == {"A": pytest.approx(4 * math.log(2))}

    def test_rerun_replaces_candidates(self, db: Database) -> None:
        seed(db, daily_payments(1, "A", {-3: 1000, -2: 1000, -1: 1000, 0: 5000}))

        assert run(db, r6_profile_deviation) == 1
        first = candidates(db, r6_profile_deviation)
        assert run(db, r6_profile_deviation) == 1
        assert candidates(db, r6_profile_deviation) == first
