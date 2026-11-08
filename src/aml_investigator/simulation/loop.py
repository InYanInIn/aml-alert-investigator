from typing import Any

import psycopg

from aml_investigator.core.sql import load
from aml_investigator.monitoring.rules import generate_candidates
from aml_investigator.simulation.world import MONITORED_BANK_ID

DELETE_EXTERNAL_CANDIDATES_SQL = load(__package__, "delete_external_candidates")


def regenerate_candidates(conn: psycopg.Connection[Any]) -> None:
    generate_candidates(conn)
    conn.execute(DELETE_EXTERNAL_CANDIDATES_SQL, {"monitored_bank_id": MONITORED_BANK_ID})
