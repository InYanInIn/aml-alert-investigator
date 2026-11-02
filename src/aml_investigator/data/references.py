from typing import Any

import psycopg

from aml_investigator.core.sql import load

FILL_REFERENCES_SQL = load(__package__, "fill_references")


def fill_references(conn: psycopg.Connection[Any]) -> int:
    return conn.execute(FILL_REFERENCES_SQL).rowcount
