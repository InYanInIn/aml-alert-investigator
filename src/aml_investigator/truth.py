from dataclasses import dataclass
from typing import Any

import psycopg

FILL_ALERT_TRUTH = """
WITH laundering AS (
    SELECT t.txn_id, t.ts, t.from_account, t.to_account
    FROM core.transactions t
    JOIN truth.transaction_labels l USING (txn_id)
    WHERE l.is_laundering
),
laundering_accounts AS (
    SELECT from_account AS account_id FROM laundering
    UNION
    SELECT to_account FROM laundering
),
in_window AS (
    SELECT a.alert_id, l.txn_id
    FROM core.alerts a
    JOIN laundering l
      ON a.account_id IN (l.from_account, l.to_account)
     AND l.ts >= a.window_start
     AND l.ts < a.window_end
)
INSERT INTO truth.alert_truth (alert_id, escalate_truth, escalate_truth_account_level, typologies)
SELECT
    a.alert_id,
    EXISTS (SELECT FROM in_window w WHERE w.alert_id = a.alert_id),
    EXISTS (SELECT FROM laundering_accounts la WHERE la.account_id = a.account_id),
    coalesce(
        (
            SELECT array_agg(DISTINCT p.typology ORDER BY p.typology)
            FROM in_window w
            JOIN truth.pattern_attempts p USING (txn_id)
            WHERE w.alert_id = a.alert_id
        ),
        '{}'
    )
FROM core.alerts a
"""

SUMMARISE_ALERT_TRUTH = """
SELECT
    count(*),
    count(*) FILTER (WHERE escalate_truth),
    count(*) FILTER (WHERE escalate_truth_account_level),
    count(*) FILTER (WHERE cardinality(typologies) > 0)
FROM truth.alert_truth
"""


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
    if conn.execute("SELECT EXISTS (SELECT FROM truth.alert_truth)").fetchone() == (True,):
        raise RuntimeError("truth.alert_truth is not empty; alert truth is computed once per alert set")
