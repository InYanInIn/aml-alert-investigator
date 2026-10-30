from typing import Any

import psycopg

RULE_ID = "R2_fan_out"
WINDOW_DAYS = 2
FLOOR = 2

DELETE_SQL = "DELETE FROM core.rule_candidates WHERE rule_id = %(rule_id)s"

INSERT_SQL = """
WITH outgoing AS (
    SELECT txn_id, from_account AS account_id, to_account, ts
    FROM core.transactions
    WHERE from_account <> to_account
),
active_days AS (
    SELECT DISTINCT account_id, ts::date AS alert_date
    FROM outgoing
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
       count(DISTINCT o.to_account),
       array_agg(o.txn_id ORDER BY o.txn_id)
FROM windows w
JOIN outgoing o ON o.account_id = w.account_id AND o.ts >= w.window_start AND o.ts < w.window_end
GROUP BY w.account_id, w.alert_date, w.window_start, w.window_end
HAVING count(DISTINCT o.to_account) >= %(floor)s::integer
"""


def generate(conn: psycopg.Connection[Any]) -> int:
    parameters = {"rule_id": RULE_ID, "window_days": WINDOW_DAYS, "floor": FLOOR}
    with conn.transaction():
        conn.execute(DELETE_SQL, parameters)
        return conn.execute(INSERT_SQL, parameters).rowcount
