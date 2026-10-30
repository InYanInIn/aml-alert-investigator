from importlib import import_module
from typing import Any, Protocol, cast

import psycopg

RULE_MODULES = ("r1_fan_in", "r2_fan_out", "r3_pass_through", "r4_cycle", "r5_structuring", "r6_profile_deviation")


class Rule(Protocol):
    RULE_ID: str

    def generate(self, conn: psycopg.Connection[Any]) -> int: ...


def rules() -> list[Rule]:
    return [cast(Rule, import_module(f"{__name__}.{name}")) for name in RULE_MODULES]


def generate_candidates(conn: psycopg.Connection[Any]) -> dict[str, int]:
    return {rule.RULE_ID: rule.generate(conn) for rule in rules()}
