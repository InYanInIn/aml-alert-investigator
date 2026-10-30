from typing import Any

import psycopg

from aml_investigator.core.sql import load

RULE_ID = "R5_structuring"
WINDOW_DAYS = 3
FLOOR = 2
REPORTING_THRESHOLD_EUR = 15000
BAND_LOWER = 0.85

DELETE_SQL = load(__package__, "delete_rule_candidates")

INSERT_SQL = load(__package__, "r5_structuring")


def generate(conn: psycopg.Connection[Any]) -> int:
    parameters = {
        "rule_id": RULE_ID,
        "window_days": WINDOW_DAYS,
        "floor": FLOOR,
        "threshold_eur": REPORTING_THRESHOLD_EUR,
        "band_floor_eur": REPORTING_THRESHOLD_EUR * BAND_LOWER,
    }
    with conn.transaction():
        conn.execute(DELETE_SQL, parameters)
        return conn.execute(INSERT_SQL, parameters).rowcount
