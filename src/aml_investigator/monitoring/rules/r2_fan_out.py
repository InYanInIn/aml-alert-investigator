from typing import Any

import psycopg

from aml_investigator.core.sql import load

RULE_ID = "R2_fan_out"
WINDOW_DAYS = 2
FLOOR = 2

DELETE_SQL = load(__package__, "delete_rule_candidates")

INSERT_SQL = load(__package__, "r2_fan_out")


def generate(conn: psycopg.Connection[Any]) -> int:
    parameters = {"rule_id": RULE_ID, "window_days": WINDOW_DAYS, "floor": FLOOR}
    with conn.transaction():
        conn.execute(DELETE_SQL, parameters)
        return conn.execute(INSERT_SQL, parameters).rowcount
