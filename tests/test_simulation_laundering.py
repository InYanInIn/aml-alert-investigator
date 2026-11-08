from datetime import timedelta
from pathlib import Path

import pytest

from aml_investigator.simulation.laundering import Laundering
from aml_investigator.simulation.world import Balances, Payment, SimulationConfig, World, build_world, live_days
from tests.simulation_fixtures import write_config

LIVE = live_days()
ROUND_DAYS = [LIVE[1], LIVE[2], LIVE[5], LIVE[6], LIVE[9], LIVE[10]]


@pytest.fixture
def config(tmp_path: Path) -> SimulationConfig:
    return write_config(tmp_path)


@pytest.fixture
def world(config: SimulationConfig) -> World:
    return build_world(9, config)


def balances(world: World) -> Balances:
    return Balances(world.opening_balances)


def run(world: World, laundering: Laundering, until: int = len(LIVE)) -> list[Payment]:
    book = balances(world)
    return [payment for day in LIVE[:until] for payment in laundering.payments_on(day, book)]


def test_steps_follow_delay_and_round_gap(world: World) -> None:
    payments = run(world, Laundering(world))

    assert sorted({payment.ts.date() for payment in payments}) == ROUND_DAYS


def test_payments_carry_the_scenario_label_and_shared_text(world: World) -> None:
    payments = run(world, Laundering(world))

    assert payments
    assert all(payment.attempt_id == 0 and payment.typology == "money_mule" for payment in payments)
    assert {payment.reference.split()[0] for payment in payments} == {"consulting_invoice", "family_support"}


def test_the_same_seed_runs_the_same_way(world: World) -> None:
    assert run(world, Laundering(world)) == run(world, Laundering(world))


def test_a_share_passes_on_that_part_of_what_the_sender_holds(world: World) -> None:
    payments = run(world, Laundering(world), until=3)
    mule = world.rosters[0].active["mule"][0]

    received = sum(p.amount_paid_eur for p in payments if p.to_account == mule)
    sent = sum(p.amount_paid_eur for p in payments if p.from_account == mule)

    assert sent == pytest.approx(0.9 * received, abs=0.02)


def test_money_reaching_the_sink_equals_the_plan_without_blocks(world: World) -> None:
    laundering = Laundering(world)
    run(world, laundering)

    [progress] = laundering.progress()
    assert progress.sink_eur == pytest.approx(progress.planned_sink_eur)
    assert progress.first_payment_day == LIVE[1]
    assert progress.first_block_day is None
    assert progress.days_to_first_block is None


def test_settling_moves_balances_for_members(world: World) -> None:
    laundering = Laundering(world)
    book = balances(world)
    mule = world.rosters[0].active["mule"][0]
    before = book.amounts[mule]

    payments = laundering.payments_on(LIVE[1], book)

    received = sum(p.amount_paid_eur for p in payments if p.to_account == mule)
    assert book.amounts[mule] == pytest.approx(before + received)


def test_a_blocked_member_is_replaced_after_two_days_and_the_group_pauses(world: World) -> None:
    laundering = Laundering(world)
    book = balances(world)
    for day in LIVE[:5]:
        laundering.payments_on(day, book)
    roster = world.rosters[0]
    blocked = roster.active["mule"][0]
    [reserve] = roster.reserves["mule"]

    laundering.block(0, blocked, LIVE[4])

    assert laundering.payments_on(LIVE[5], book) == []
    assert laundering.payments_on(LIVE[6], book) == []
    resumed = laundering.payments_on(LIVE[7], book)
    assert resumed
    assert reserve in {payment.to_account for payment in resumed}
    assert blocked not in {payment.to_account for payment in resumed}
    assert blocked not in {payment.from_account for payment in resumed}
    [progress] = laundering.progress()
    assert progress.first_block_day == LIVE[5]
    assert progress.days_to_first_block == 4


def test_a_group_stops_when_no_reserve_is_left(world: World) -> None:
    laundering = Laundering(world)
    book = balances(world)
    roster = world.rosters[0]
    laundering.payments_on(LIVE[1], book)
    laundering.block(0, roster.active["mule"][0], LIVE[1])
    laundering.block(0, roster.active["mule"][1], LIVE[1])

    assert [laundering.payments_on(day, book) for day in LIVE[2:]] == [[] for _ in LIVE[2:]]
    [progress] = laundering.progress()
    assert progress.stopped_on == LIVE[2]


def test_blocking_a_reserve_uses_it_up_without_pausing(world: World) -> None:
    laundering = Laundering(world)
    book = balances(world)
    roster = world.rosters[0]
    [reserve] = roster.reserves["mule"]
    laundering.payments_on(LIVE[1], book)

    laundering.block(0, reserve, LIVE[1])
    assert laundering.payments_on(LIVE[2], book)
    laundering.block(0, roster.active["mule"][0], LIVE[2])

    [progress] = laundering.progress()
    assert progress.stopped_on == LIVE[3]


def test_blocking_a_sibling_account_finds_the_group_but_leaves_it_running(world: World) -> None:
    laundering = Laundering(world)
    book = balances(world)
    laundering.payments_on(LIVE[1], book)

    laundering.block(0, "not-a-roster-account", LIVE[1])

    assert laundering.payments_on(LIVE[2], book)
    [progress] = laundering.progress()
    assert progress.first_block_day == LIVE[2]
    assert progress.stopped_on is None


def test_frozen_money_never_reaches_the_sink(world: World) -> None:
    laundering = Laundering(world)
    book = balances(world)
    roster = world.rosters[0]
    laundering.payments_on(LIVE[1], book)
    laundering.block(0, roster.active["mule"][0], LIVE[1])
    for day in LIVE[2:]:
        laundering.payments_on(day, book)

    [progress] = laundering.progress()
    assert 0 < progress.sink_eur < progress.planned_sink_eur


def test_block_day_is_the_day_after_the_decision(world: World) -> None:
    laundering = Laundering(world)
    laundering.block(0, world.rosters[0].active["mule"][0], LIVE[3])

    [progress] = laundering.progress()
    assert progress.first_block_day == LIVE[3] + timedelta(days=1)
