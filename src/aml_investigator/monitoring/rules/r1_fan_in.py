from typing import Any

import psycopg

from aml_investigator.core.sql import load

RULE_ID = "R1_fan_in"
WINDOW_DAYS = 2
FLOOR = 2

DELETE_SQL = "DELETE FROM core.rule_candidates WHERE rule_id = %(rule_id)s"

INSERT_SQL = load(__package__, "r1_fan_in")


def generate(conn: psycopg.Connection[Any]) -> int:
    parameters = {"rule_id": RULE_ID, "window_days": WINDOW_DAYS, "floor": FLOOR}
    with conn.transaction():
        conn.execute(DELETE_SQL, parameters)
        return conn.execute(INSERT_SQL, parameters).rowcount
