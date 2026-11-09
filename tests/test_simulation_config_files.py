import re
from pathlib import Path

from aml_investigator.simulation.laundering import Laundering
from aml_investigator.simulation.world import Balances, SimulationConfig, build_world, live_days, load_config

CONFIG = Path("config/simulation")
REQUIRED_TYPOLOGIES = {
    "money_mule",
    "structuring",
    "shell_pass_through",
    "funnel_account",
    "cycle",
    "invoice_disguise",
    "slow_layering",
}
MIN_REFERENCE_TEXTS = 8
MIN_PROFILE_TEXTS = 12
MIN_COMPANY_NAMES = 80
LABEL_WORDS = re.compile(
    r"\b(?:launder\w*|mules?|smurf\w*|shells?|structuring|layering|suspicious|fraud\w*|illegal|dirty)\b", re.IGNORECASE
)


def config() -> SimulationConfig:
    return load_config(CONFIG)


def test_committed_config_loads_and_builds_a_world() -> None:
    world = build_world(1, config())

    assert 800 <= sum(len(c.accounts) for c in world.customers if c.group_id is None) <= 880


def test_every_committed_scenario_runs_through_the_interpreter() -> None:
    loaded = config()
    for seed in (1, 2, 3):
        world = build_world(seed, loaded)
        laundering = Laundering(world)
        book = Balances(world.opening_balances)
        payments = [payment for day in live_days() for payment in laundering.payments_on(day, book)]

        assert {payment.attempt_id for payment in payments} == {roster.group_id for roster in world.rosters}
        assert all(payment.from_account != payment.to_account for payment in payments)
        assert all(group.planned_sink_eur > 0 for group in laundering.progress())


def test_seven_or_eight_groups_cover_every_named_typology() -> None:
    scenarios = config().scenarios.scenarios

    assert 7 <= len(scenarios) <= 8
    assert {scenario.typology for scenario in scenarios} >= REQUIRED_TYPOLOGIES
    assert len({scenario.id for scenario in scenarios}) == len(scenarios)


def test_about_twenty_accounts_are_in_laundering_groups() -> None:
    loaded = config()
    expected = 0.0
    for scenario in loaded.scenarios.scenarios:
        for role in scenario.roles:
            if role.persona is not None:
                low, high = loaded.personas.personas[role.persona].accounts_per_customer
                expected += (role.count + role.reserves) * (low + high) / 2

    assert 15 <= expected <= 26


def test_reserves_exist_so_a_block_can_be_absorbed() -> None:
    assert all(any(role.reserves for role in s.roles) for s in config().scenarios.scenarios)


def test_mismatch_share_is_set_by_the_author() -> None:
    assert 0 < config().scenarios.honest_profile_mismatch_share < 0.5


def test_text_pools_are_large_enough_to_share() -> None:
    pools = config().pools

    assert all(len(texts) >= MIN_REFERENCE_TEXTS for texts in pools.references.values())
    assert set(pools.profiles) == {"student", "worker", "office_employee", "business_owner", "corporation"}
    for profile in pools.profiles.values():
        for texts in (
            profile.occupation,
            profile.business_description,
            profile.account_purpose,
            profile.expected_activity,
        ):
            assert len(texts) >= MIN_PROFILE_TEXTS
    assert len(pools.company_names) >= MIN_COMPANY_NAMES


def test_no_text_names_the_offence() -> None:
    pools = config().pools
    texts = [text for pool in pools.references.values() for text in pool] + list(pools.company_names)
    for profile in pools.profiles.values():
        texts += [
            *profile.occupation,
            *profile.business_description,
            *profile.account_purpose,
            *profile.expected_activity,
        ]

    offending = [text for text in texts if LABEL_WORDS.search(text)]

    assert not offending, offending
