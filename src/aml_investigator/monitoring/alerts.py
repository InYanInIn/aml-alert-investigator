from collections.abc import Mapping
from datetime import date
from typing import Any

import psycopg

from aml_investigator.core.sql import load
from aml_investigator.data.splits import DayRange
from aml_investigator.monitoring.rules import rules

ALL_DAYS = DayRange(date.min, date.max)

FIRST_DAYS_CTES = load(__package__, "first_days_ctes")

BUILD_SQL = f"{FIRST_DAYS_CTES}\n{load(__package__, 'build_alerts')}"

ALERTS_EXIST_SQL = load(__package__, "alerts_exist")


def first_days_parameters(
    thresholds: Mapping[str, float], days: DayRange, *, suppression_days: int
) -> dict[str, object]:
    unknown = sorted(set(thresholds) - {rule.RULE_ID for rule in rules()})
    if unknown:
        raise ValueError(f"thresholds for unknown rules: {', '.join(unknown)}")
    return {
        "rule_ids": list(thresholds),
        "limits": [float(limit) for limit in thresholds.values()],
        "first_day": days.first,
        "last_day": days.last,
        "suppression_days": suppression_days,
    }


def build_alerts(conn: psycopg.Connection[Any], thresholds: Mapping[str, float], *, suppression_days: int) -> int:
    parameters = first_days_parameters(thresholds, ALL_DAYS, suppression_days=suppression_days)
    with conn.transaction():
        if conn.execute(ALERTS_EXIST_SQL).fetchone() == (True,):
            raise RuntimeError("core.alerts is not empty; refusing to build alerts on top of existing ones")
        return conn.execute(BUILD_SQL, parameters).rowcount
