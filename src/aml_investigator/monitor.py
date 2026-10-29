from typing import Any

import psycopg

FAN_OUT = "R2_fan_out"

FAN_OUT_SQL = """
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
),
fan_outs AS (
    SELECT w.account_id,
           w.alert_date,
           w.window_start,
           w.window_end,
           count(DISTINCT o.to_account) AS receivers,
           array_agg(o.txn_id ORDER BY o.txn_id) AS evidence_txn_ids
    FROM windows w
    JOIN outgoing o ON o.account_id = w.account_id AND o.ts >= w.window_start AND o.ts < w.window_end
    GROUP BY w.account_id, w.alert_date, w.window_start, w.window_end
    HAVING count(DISTINCT o.to_account) >= %(min_receivers)s::integer
),
created AS (
    INSERT INTO core.alerts (account_id, alert_date, window_start, window_end)
    SELECT account_id, alert_date, window_start, window_end
    FROM fan_outs
    ON CONFLICT (account_id, alert_date) DO NOTHING
    RETURNING alert_id, account_id, alert_date
),
known AS (
    SELECT alert_id, account_id, alert_date FROM core.alerts
    UNION ALL
    SELECT alert_id, account_id, alert_date FROM created
),
hits AS (
    INSERT INTO core.alert_rule_hits (alert_id, rule_id, score, evidence_txn_ids)
    SELECT k.alert_id, %(rule_id)s, f.receivers, f.evidence_txn_ids
    FROM fan_outs f
    JOIN known k ON k.account_id = f.account_id AND k.alert_date = f.alert_date
    ON CONFLICT (alert_id, rule_id) DO NOTHING
)
SELECT alert_id FROM created
"""


def run_fan_out(conn: psycopg.Connection[Any], *, min_receivers: int, window_days: int) -> int:
    created = conn.execute(
        FAN_OUT_SQL,
        {"min_receivers": min_receivers, "window_days": window_days, "rule_id": FAN_OUT},
    ).rowcount
    conn.commit()
    return created
