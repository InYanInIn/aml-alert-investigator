from decimal import Decimal
from typing import Any

import psycopg

from aml_investigator.core.sql import load

RULE_ID = "R6_profile_deviation"
WINDOW_DAYS = 1
FLOOR = 2
MIN_HISTORY_DAYS = 3
MIN_LOG_STDDEV = Decimal("0.25")
MIN_DAY_EUR = 1000

DELETE_SQL = "DELETE FROM core.rule_candidates WHERE rule_id = %(rule_id)s"

INSERT_SQL = load(__package__, "r6_profile_deviation")


def generate(conn: psycopg.Connection[Any]) -> int:
    parameters = {
        "rule_id": RULE_ID,
        "window_days": WINDOW_DAYS,
        "floor": FLOOR,
        "min_history_days": MIN_HISTORY_DAYS,
        "min_log_stddev": MIN_LOG_STDDEV,
        "min_day_eur": MIN_DAY_EUR,
    }
    with conn.transaction():
        conn.execute(DELETE_SQL, parameters)
        return conn.execute(INSERT_SQL, parameters).rowcount
