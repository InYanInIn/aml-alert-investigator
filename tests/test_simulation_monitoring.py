from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from aml_investigator.simulation.behaviour import normal_day
from aml_investigator.simulation.laundering import Laundering
from aml_investigator.simulation.loop import regenerate_candidates
from aml_investigator.simulation.world import (
    Balances,
    Tape,
    World,
    build_world,
    history_days,
    insert_population,
    live_days,
    reset_world,
)
from tests.conftest import Database
from tests.simulation_fixtures import write_config

CANDIDATES_UP_TO = (
    "SELECT rule_id, account_id, alert_date, window_start, window_end, score, evidence_txn_ids "
    "FROM core.rule_candidates WHERE alert_date <= %s ORDER BY rule_id, account_id, alert_date"
)


def tape_until(db: Database, world: World, last: date) -> list[tuple[Any, ...]]:
    balances = Balances(world.opening_balances)
    laundering = Laundering(world)
    tape = Tape(world.seed)
    with db.owner() as conn:
        reset_world(conn)
        insert_population(conn, world)
        for day in (*history_days(), *live_days()):
            if day <= last:
                payments = normal_day(world, day, balances, frozenset())
                tape.append(conn, [*payments, *laundering.payments_on(day, balances)])
        regenerate_candidates(conn)
        return conn.execute(CANDIDATES_UP_TO, (live_days()[10],)).fetchall()


def test_candidates_up_to_a_day_do_not_depend_on_later_days(world_db: Database, tmp_path: Path) -> None:
    world = build_world(21, write_config(tmp_path))

    ending_on_the_day = tape_until(world_db, world, live_days()[10])
    continuing = tape_until(world_db, world, live_days()[-1])

    assert ending_on_the_day
    assert ending_on_the_day == continuing


def add_account(db: Database, account: str, bank: int) -> None:
    with db.owner() as conn:
        conn.execute("INSERT INTO core.accounts VALUES (%s, %s, 'Bank', %s, 'Corporation')", (account, bank, account))


def add_payment(db: Database, txn_id: int, sender: str, receiver: str) -> None:
    with db.owner() as conn:
        conn.execute(
            "INSERT INTO core.transactions (txn_id, ts, from_account, to_account, amount_paid, paid_currency, "
            "amount_received, received_currency, payment_format, amount_paid_eur) "
            "VALUES (%s, %s, %s, %s, 500, 'Euro', 500, 'Euro', 'ACH', 500)",
            (txn_id, datetime(2025, 3, 24, 10) + timedelta(minutes=txn_id), sender, receiver),
        )


def test_external_accounts_lose_their_candidates(world_db: Database) -> None:
    for account, bank in (("MONITORED", 1), ("EXTERNAL", 7), ("S1", 7), ("S2", 7)):
        add_account(world_db, account, bank)
    for txn_id, (sender, receiver) in enumerate(
        [("S1", "MONITORED"), ("S2", "MONITORED"), ("S1", "EXTERNAL"), ("S2", "EXTERNAL")], start=1
    ):
        add_payment(world_db, txn_id, sender, receiver)

    with world_db.owner() as conn:
        regenerate_candidates(conn)
        accounts = conn.execute("SELECT DISTINCT account_id FROM core.rule_candidates").fetchall()

    assert accounts == [("MONITORED",)]
