import random
from pathlib import Path

import pytest
import yaml

from aml_investigator.simulation.behaviour import normal_day, poisson
from aml_investigator.simulation.world import (
    FIRST_DAY,
    Balances,
    Payment,
    SimulationConfig,
    World,
    build_world,
    history_days,
    live_days,
    load_config,
)
from tests.simulation_fixtures import write_config

ALL_DAYS = (*history_days(), *live_days())


@pytest.fixture
def config(tmp_path: Path) -> SimulationConfig:
    return write_config(tmp_path, scenarios=[])


def tape(world: World, blocked: frozenset[str] = frozenset()) -> list[Payment]:
    balances = Balances(world.opening_balances)
    return [payment for day in ALL_DAYS for payment in normal_day(world, day, balances, blocked)]


def accounts_of(world: World, persona: str) -> set[str]:
    return set(world.persona_accounts[persona])


def references(payments: list[Payment], key: str) -> list[Payment]:
    return [payment for payment in payments if payment.reference.startswith(key)]


def test_poisson_has_the_requested_mean() -> None:
    rng = random.Random(3)  # noqa: S311

    draws = [poisson(rng, 2.5) for _ in range(20_000)]

    assert sum(draws) / len(draws) == pytest.approx(2.5, abs=0.05)


def test_the_same_seed_produces_the_same_days(config: SimulationConfig) -> None:
    assert tape(build_world(5, config)) == tape(build_world(5, config))


def test_spending_stops_at_the_balance(config: SimulationConfig) -> None:
    world = build_world(5, config)
    balances = Balances(world.opening_balances)

    for day in ALL_DAYS:
        normal_day(world, day, balances, frozenset())
        assert min(balances.amounts.values()) >= -0.01


def test_blocked_accounts_neither_send_nor_receive(config: SimulationConfig) -> None:
    world = build_world(5, config)
    blocked = frozenset(sorted(accounts_of(world, "student"))[:3])

    payments = tape(world, blocked)

    assert not [p for p in payments if p.from_account in blocked or p.to_account in blocked]


def test_blocking_one_account_leaves_unrelated_accounts_unchanged(config: SimulationConfig) -> None:
    world = build_world(5, config)
    corporations = accounts_of(world, "corporation")
    blocked = frozenset(sorted(accounts_of(world, "student"))[:1])

    with_block = [p for p in tape(world, blocked) if p.from_account in corporations or p.to_account in corporations]
    without = [p for p in tape(world) if p.from_account in corporations or p.to_account in corporations]

    assert with_block == without


def test_monthly_events_happen_on_their_day(config: SimulationConfig) -> None:
    payments = tape(build_world(5, config))

    assert {payment.ts.day for payment in references(payments, "payroll")} == {25}


def test_every_reference_is_a_rendered_shared_text(config: SimulationConfig) -> None:
    payments = tape(build_world(5, config))

    assert payments
    assert all(payment.reference and "{" not in payment.reference for payment in payments)


def test_a_parent_transfer_paid_straight_on_as_tuition(config: SimulationConfig) -> None:
    world = build_world(5, config)
    payments = tape(world)
    tuition = references(payments, "tuition")
    support = references(payments, "family_support")

    assert tuition
    for payment in tuition:
        assert any(
            other.to_account == payment.from_account
            and other.ts.date() == payment.ts.date()
            and other.amount_paid == pytest.approx(payment.amount_paid)
            for other in support
        )


def test_currency_exchange_is_a_payment_to_oneself_that_changes_currency(config: SimulationConfig) -> None:
    exchanges = references(tape(build_world(5, config)), "currency_exchange")

    assert exchanges
    for payment in exchanges:
        assert payment.from_account == payment.to_account
        assert (payment.paid_currency, payment.received_currency) == ("Euro", "US Dollar")
        assert payment.amount_received == pytest.approx(payment.amount_paid / 0.9, abs=0.01)


def test_transfers_between_own_accounts_stay_within_the_customer(config: SimulationConfig) -> None:
    world = build_world(5, config)
    entity_of = {account: customer.entity_id for customer in world.customers for account in customer.accounts}
    transfers = references(tape(world), "own_transfer")

    assert transfers
    for payment in transfers:
        assert payment.from_account != payment.to_account
        assert entity_of[payment.from_account] == entity_of[payment.to_account]


def test_peer_payments_never_go_to_oneself(config: SimulationConfig) -> None:
    transfers = references(tape(build_world(5, config)), "peer_transfer")

    assert transfers
    assert all(payment.from_account != payment.to_account for payment in transfers)


def consulting_payments_to_students(tmp_path: Path, share: float) -> list[Payment]:
    world = build_world(5, write_config(tmp_path, scenarios=[], mismatch_share=share))
    students = accounts_of(world, "student")
    return [p for p in references(tape(world), "consulting_invoice") if p.to_account in students]


def test_honest_customers_can_mismatch_their_profile(tmp_path: Path) -> None:
    assert consulting_payments_to_students(tmp_path / "all", 1.0)
    assert not consulting_payments_to_students(tmp_path / "none", 0.0)


def test_a_confounder_can_be_limited_to_a_window_of_days(tmp_path: Path) -> None:
    write_config(tmp_path, scenarios=[])
    personas = yaml.safe_load((tmp_path / "personas.yaml").read_text())
    exchange = personas["personas"]["student"]["confounders"][1]
    exchange["first_day_index"] = exchange["last_day_index"] = 2
    exchange["rate_per_day"] = 1.0
    (tmp_path / "personas.yaml").write_text(yaml.safe_dump(personas))

    exchanges = references(tape(build_world(5, load_config(tmp_path))), "currency_exchange")

    assert {payment.ts.date() for payment in exchanges} == {history_days()[2]}
    assert history_days()[2] != FIRST_DAY
