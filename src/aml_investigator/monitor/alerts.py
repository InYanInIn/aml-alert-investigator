from collections.abc import Mapping
from datetime import date
from typing import Any

import psycopg

from aml_investigator.monitor import rules
from aml_investigator.splits import DayRange

ALL_DAYS = DayRange(date.min, date.max)

FIRST_DAYS_CTES = """
thresholds AS (
    SELECT rule_id, threshold
    FROM unnest(%(rule_ids)s::text[], %(limits)s::numeric[]) AS t(rule_id, threshold)
),
qualifying AS (
    SELECT c.*
    FROM core.rule_candidates c
    JOIN thresholds t ON t.rule_id = c.rule_id AND c.score >= t.threshold
    WHERE c.alert_date BETWEEN %(first_day)s::date - %(suppression_days)s::integer AND %(last_day)s::date
),
first_days AS (
    SELECT q.*
    FROM qualifying q
    WHERE q.alert_date >= %(first_day)s::date
      AND NOT EXISTS (
        SELECT 1
        FROM qualifying earlier
        WHERE earlier.rule_id = q.rule_id
          AND earlier.account_id = q.account_id
          AND earlier.alert_date BETWEEN q.alert_date - %(suppression_days)s::integer AND q.alert_date - 1
    )
),
account_days AS (
    SELECT account_id, alert_date, min(window_start) AS window_start, max(window_end) AS window_end
    FROM first_days
    GROUP BY account_id, alert_date
)
"""

BUILD_SQL = f"""
WITH {FIRST_DAYS_CTES},
created AS (
    INSERT INTO core.alerts (account_id, alert_date, window_start, window_end)
    SELECT account_id, alert_date, window_start, window_end
    FROM account_days
    RETURNING alert_id, account_id, alert_date
),
hits AS (
    INSERT INTO core.alert_rule_hits (alert_id, rule_id, score, evidence_txn_ids)
    SELECT c.alert_id, f.rule_id, f.score, f.evidence_txn_ids
    FROM first_days f
    JOIN created c ON c.account_id = f.account_id AND c.alert_date = f.alert_date
)
SELECT alert_id FROM created
"""  # noqa: S608


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
        if conn.execute("SELECT EXISTS (SELECT 1 FROM core.alerts)").fetchone() == (True,):
            raise RuntimeError("core.alerts is not empty; refusing to build alerts on top of existing ones")
        return conn.execute(BUILD_SQL, parameters).rowcount
