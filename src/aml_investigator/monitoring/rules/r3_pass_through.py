from decimal import Decimal
from typing import Any

import psycopg

from aml_investigator.core.sql import load

RULE_ID = "R3_pass_through"
WINDOW_DAYS = 2
FLOOR = 1000
RATIO_LOWER = Decimal("0.8")
RATIO_UPPER = Decimal("1.2")

DELETE_SQL = "DELETE FROM core.rule_candidates WHERE rule_id = %(rule_id)s"

INSERT_SQL = load(__package__, "r3_pass_through")


def generate(conn: psycopg.Connection[Any]) -> int:
    parameters = {
        "rule_id": RULE_ID,
        "window_days": WINDOW_DAYS,
        "floor": FLOOR,
        "ratio_lower": RATIO_LOWER,
        "ratio_upper": RATIO_UPPER,
    }
    with conn.transaction():
        conn.execute(DELETE_SQL, parameters)
        return conn.execute(INSERT_SQL, parameters).rowcount
