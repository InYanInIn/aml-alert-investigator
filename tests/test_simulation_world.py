import random
import re
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest
import yaml

from aml_investigator.simulation.world import (
    FIRST_DAY,
    Balances,
    Payment,
    SimulationConfig,
    Tape,
    build_world,
    history_days,
    insert_population,
    live_days,
    load_config,
    make_payment,
    reset_world,
)
from tests.conftest import Database
from tests.simulation_fixtures import SCENARIO, write_config


@pytest.fixture
def config(tmp_path: Path) -> SimulationConfig:
    return write_config(tmp_path)


def test_the_timeline_is_21_days_of_history_then_28_live_days() -> None:
    assert history_days()[0] == FIRST_DAY
    assert len(history_days()) == 21
    assert len(live_days()) == 28
    assert (live_days()[0] - history_days()[-1]).days == 1


def test_the_same_seed_builds_the_same_world(config: SimulationConfig) -> None:
    assert build_world(7, config) == build_world(7, config)


def test_another_seed_builds_other_identifiers(config: SimulationConfig) -> None:
    first = {account for customer in build_world(7, config).customers for account in customer.accounts}
    second = {account for customer in build_world(8, config).customers for account in customer.accounts}

    assert not first & second


def test_account_ids_are_pseudonyms_that_do_not_show_the_persona(config: SimulationConfig) -> None:
    world = build_world(7, config)

    for customer in world.customers:
        assert re.fullmatch(r"E[0-9a-f]{12}", customer.entity_id)
        for account in customer.accounts:
            assert re.fullmatch(r"A[0-9a-f]{12}", account)


def test_personas_reach_their_account_targets_within_one_customer(config: SimulationConfig) -> None:
    world = build_world(7, config)
    honest = [customer for customer in world.customers if customer.group_id is None]

    students = sum(len(customer.accounts) for customer in honest if customer.persona == "student")
    corporations = sum(len(customer.accounts) for customer in honest if customer.persona == "corporation")

    assert 12 <= students < 12 + 2
    assert 8 <= corporations < 8 + 4
    assert all(1 <= len(customer.accounts) <= 2 for customer in honest if customer.persona == "student")


def test_group_members_hide_among_their_persona(config: SimulationConfig) -> None:
    world = build_world(7, config)
    members = [customer for customer in world.customers if customer.group_id == 0]
    roster = world.rosters[0]

    assert len(members) == 3
    assert all(customer.persona == "student" and customer.scenario == "mules" for customer in members)
    assert not any(customer.mismatch for customer in members)
    assert len(roster.active["mule"]) == 2
    assert len(roster.reserves["mule"]) == 1
    assert set(roster.active["mule"]) | set(roster.reserves["mule"]) == {customer.accounts[0] for customer in members}


def test_a_group_member_has_the_profile_of_its_persona(config: SimulationConfig) -> None:
    world = build_world(7, config)
    member = next(customer for customer in world.customers if customer.group_id == 0)
    profile = next(profile for profile in world.profiles if profile.entity_id == member.entity_id)

    assert profile.occupation == "student occupation"
    assert profile.account_purpose == "student purpose"


def test_companies_have_a_display_name_and_individuals_do_not(config: SimulationConfig) -> None:
    world = build_world(7, config)
    by_entity = {profile.entity_id: profile for profile in world.profiles}

    for customer in world.customers:
        named = by_entity[customer.entity_id].display_name is not None
        assert named == (customer.entity_type != "Individual")


def test_external_companies_can_be_looked_up_by_name_only(config: SimulationConfig) -> None:
    world = build_world(7, config)
    by_entity = {profile.entity_id: profile for profile in world.profiles}

    companies = [external for external in world.externals if external.entity_type == "Corporation"]

    assert companies
    for external in companies:
        profile = by_entity[external.entity_id]
        assert profile.display_name in ("Alder Works", "Birch Trading", "Cedar Holdings")
        assert profile.occupation is None
        assert profile.declared_monthly_eur is None


def test_mismatching_honest_customers_appear_at_the_configured_share(tmp_path: Path) -> None:
    config = write_config(tmp_path, mismatch_share=0.5)

    honest = [c for seed in range(30) for c in build_world(seed, config).customers if c.group_id is None]
    share = sum(customer.mismatch for customer in honest) / len(honest)

    assert 0.4 < share < 0.6


def test_a_reference_key_used_only_by_launderers_is_refused(tmp_path: Path) -> None:
    write_config(tmp_path)
    scenario: dict[str, Any] = {**SCENARIO, "steps": [{**SCENARIO["steps"][0], "reference": "payroll_secret"}]}
    (tmp_path / "scenarios.yaml").write_text(
        yaml.safe_dump({"honest_profile_mismatch_share": 0.1, "scenarios": [scenario]})
    )

    with pytest.raises(ValueError, match="payroll_secret"):
        load_config(tmp_path)


def test_a_reference_key_no_honest_customer_of_the_persona_uses_is_refused(tmp_path: Path) -> None:
    write_config(tmp_path)
    outsider = {**SCENARIO["steps"][0], "reference": "payroll"}
    scenario: dict[str, Any] = {**SCENARIO, "steps": [outsider, SCENARIO["steps"][1]]}
    (tmp_path / "scenarios.yaml").write_text(
        yaml.safe_dump({"honest_profile_mismatch_share": 0.1, "scenarios": [scenario]})
    )

    with pytest.raises(ValueError, match="student:payroll"):
        load_config(tmp_path)


def test_a_reference_text_with_an_unknown_placeholder_is_refused(tmp_path: Path) -> None:
    write_config(tmp_path)
    pools = yaml.safe_load((tmp_path / "text_pools.yaml").read_text())
    pools["references"]["groceries"] = ["Groceries {customer}"]
    (tmp_path / "text_pools.yaml").write_text(yaml.safe_dump(pools))

    with pytest.raises(ValueError, match="customer"):
        load_config(tmp_path)


def test_a_step_needs_exactly_one_of_amount_and_share(tmp_path: Path) -> None:
    write_config(tmp_path)
    bad = {**SCENARIO["steps"][0], "share": 0.5}
    scenario = {**SCENARIO, "steps": [bad, SCENARIO["steps"][1]]}
    (tmp_path / "scenarios.yaml").write_text(
        yaml.safe_dump({"honest_profile_mismatch_share": 0.1, "scenarios": [scenario]})
    )

    with pytest.raises(ValueError, match="exactly one"):
        load_config(tmp_path)


def test_make_payment_renders_a_shared_reference_and_converts_eur(config: SimulationConfig) -> None:
    world = build_world(7, config)

    rng = random.Random(1)  # noqa: S311
    payment = make_payment(world, rng, FIRST_DAY, "A", "B", 90.0, "ACH", "groceries", received_currency="US Dollar")

    assert payment.amount_paid == 90.0
    assert payment.paid_currency == "Euro"
    assert payment.amount_received == 100.0
    assert payment.received_currency == "US Dollar"
    assert payment.amount_paid_eur == 90.0
    assert payment.reference.startswith("groceries")
    assert "{" not in payment.reference
    assert payment.ts.date() == FIRST_DAY
    assert not payment.is_laundering


def test_balances_cap_spending_and_ignore_externals() -> None:
    balances = Balances({"A": 100.0})

    assert balances.affordable("A", 150.0) == 100.0
    assert balances.affordable("X", 150.0) == 150.0


def payment_between(sender: str, receiver: str, minute: int = 0, *, attempt_id: int | None = None) -> Payment:
    return Payment(
        ts=datetime(2025, 3, 3, 9, minute),
        from_account=sender,
        to_account=receiver,
        amount_paid=10.0,
        paid_currency="Euro",
        amount_received=10.0,
        received_currency="Euro",
        payment_format="ACH",
        amount_paid_eur=10.0,
        reference="groceries",
        attempt_id=attempt_id,
        typology="money_mule" if attempt_id is not None else None,
    )


def test_balances_settle_both_sides_and_leave_self_payments_alone() -> None:
    balances = Balances({"A": 100.0, "B": 5.0})

    balances.settle(payment_between("A", "B"))
    balances.settle(payment_between("B", "B"))
    balances.settle(payment_between("B", "X"))

    assert balances.amounts == {"A": 90.0, "B": 5.0}


def test_insert_population_writes_accounts_profiles_and_actors(world_db: Database, tmp_path: Path) -> None:
    world = build_world(7, write_config(tmp_path))

    with world_db.owner() as conn:
        insert_population(conn, world)
        monitored = conn.execute("SELECT count(*) FROM core.accounts WHERE bank_id = 1").fetchone()
        external = conn.execute("SELECT count(*) FROM core.accounts WHERE bank_id <> 1").fetchone()
        actors = conn.execute("SELECT count(*), count(group_id) FROM truth.sim_actors").fetchone()
        names = conn.execute("SELECT count(*) FROM core.customer_profiles WHERE display_name IS NOT NULL").fetchone()
        rates = conn.execute("SELECT count(*) FROM core.fx_rates").fetchone()

    accounts = sum(len(customer.accounts) for customer in world.customers)
    members = sum(len(customer.accounts) for customer in world.customers if customer.group_id is not None)
    assert monitored == (accounts,)
    assert external == (len(world.externals),)
    assert actors == (accounts, members)
    assert names is not None
    assert int(str(names[0])) > 0
    assert rates == (2,)


def seeded_tape(db: Database, tmp_path: Path, payments: list[Payment]) -> Tape:
    world = build_world(7, write_config(tmp_path))
    accounts = [customer.accounts[0] for customer in world.customers[:3]]
    renamed = [
        replace(p, from_account=accounts[int(p.from_account)], to_account=accounts[int(p.to_account)]) for p in payments
    ]
    tape = Tape(7)
    with db.owner() as conn:
        insert_population(conn, world)
        tape.append(conn, renamed)
    return tape


def test_transaction_ids_do_not_depend_on_generation_order(world_db: Database, tmp_path: Path) -> None:
    payments = [payment_between("0", "1", minute=5), payment_between("1", "2"), payment_between("2", "0", attempt_id=0)]

    forward = seeded_tape(world_db, tmp_path, payments).sha256
    with world_db.owner() as conn:
        reset_world(conn)
    backward = seeded_tape(world_db, tmp_path, list(reversed(payments))).sha256

    assert forward == backward


def test_tape_numbers_by_time_then_hash_and_writes_truth(world_db: Database, tmp_path: Path) -> None:
    payments = [payment_between("0", "1", minute=5), payment_between("1", "2"), payment_between("2", "0", attempt_id=0)]

    seeded_tape(world_db, tmp_path, payments)

    with world_db.owner() as conn:
        minutes = conn.execute("SELECT extract(minute FROM ts)::int FROM core.transactions ORDER BY txn_id").fetchall()
        ids = conn.execute("SELECT txn_id FROM core.transactions ORDER BY txn_id").fetchall()
        labels = conn.execute(
            "SELECT count(*), count(*) FILTER (WHERE is_laundering) FROM truth.transaction_labels"
        ).fetchone()
        attempts = conn.execute("SELECT attempt_id, typology FROM truth.pattern_attempts").fetchall()
    assert minutes == [(0,), (0,), (5,)]
    assert ids == [(1,), (2,), (3,)]
    assert labels == (3, 1)
    assert attempts == [(0, "money_mule")]


def test_tape_continues_numbering_on_the_next_day(world_db: Database, tmp_path: Path) -> None:
    world = build_world(7, write_config(tmp_path))
    accounts = [customer.accounts[0] for customer in world.customers[:2]]
    tape = Tape(7)
    first = replace(payment_between("A", "B"), from_account=accounts[0], to_account=accounts[1])
    second = replace(first, ts=datetime(2025, 3, 4, 9))

    with world_db.owner() as conn:
        insert_population(conn, world)
        tape.append(conn, [first])
        tape.append(conn, [second])
        assert conn.execute("SELECT txn_id FROM core.transactions ORDER BY txn_id").fetchall() == [(1,), (2,)]
