from typing import Any

import psycopg

RULE_ID = "R5_structuring"
WINDOW_DAYS = 3
FLOOR = 2
REPORTING_THRESHOLD_EUR = 15000
BAND_LOWER = 0.85

DELETE_SQL = "DELETE FROM core.rule_candidates WHERE rule_id = %(rule_id)s"

INSERT_SQL = """
WITH near_threshold AS (
    SELECT txn_id, from_account AS account_id, ts
    FROM core.transactions
    WHERE from_account <> to_account
      AND amount_paid_eur >= %(band_floor_eur)s::numeric
      AND amount_paid_eur < %(threshold_eur)s::numeric
),
active_days AS (
    SELECT DISTINCT account_id, ts::date AS alert_date
    FROM near_threshold
),
windows AS (
    SELECT account_id,
           alert_date,
           (alert_date - (%(window_days)s::integer - 1))::timestamp AS window_start,
           (alert_date + 1)::timestamp AS window_end
    FROM active_days
)
INSERT INTO core.rule_candidates (rule_id, account_id, alert_date, window_start, window_end, score, evidence_txn_ids)
SELECT %(rule_id)s,
       w.account_id,
       w.alert_date,
       w.window_start,
       w.window_end,
       count(*),
       array_agg(n.txn_id ORDER BY n.txn_id)
FROM windows w
JOIN near_threshold n ON n.account_id = w.account_id AND n.ts >= w.window_start AND n.ts < w.window_end
GROUP BY w.account_id, w.alert_date, w.window_start, w.window_end
HAVING count(*) >= %(floor)s::integer
"""


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
