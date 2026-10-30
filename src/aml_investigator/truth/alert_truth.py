from dataclasses import dataclass
from typing import Any

import psycopg

from aml_investigator.core.sql import load

FILL_ALERT_TRUTH = load(__package__, "fill_alert_truth")

SUMMARISE_ALERT_TRUTH = load(__package__, "summarise_alert_truth")

ALERT_TRUTH_EXISTS = load(__package__, "alert_truth_exists")


@dataclass(frozen=True)
class TruthReport:
    alerts: int
    escalate_true: int
    account_level_true: int
    with_typology: int

    def __str__(self) -> str:
        return (
            f"alerts: {self.alerts}, escalate true: {self.escalate_true}, "
            f"account-level true: {self.account_level_true}, with typology: {self.with_typology}"
        )


def compute_alert_truth(conn: psycopg.Connection[Any]) -> TruthReport:
    with conn.transaction():
        refuse_non_empty(conn)
        conn.execute(FILL_ALERT_TRUTH)
        [summary] = conn.execute(SUMMARISE_ALERT_TRUTH).fetchall()
    return TruthReport(*summary)


def refuse_non_empty(conn: psycopg.Connection[Any]) -> None:
    if conn.execute(ALERT_TRUTH_EXISTS).fetchone() == (True,):
        raise RuntimeError("truth.alert_truth is not empty; alert truth is computed once per alert set")
