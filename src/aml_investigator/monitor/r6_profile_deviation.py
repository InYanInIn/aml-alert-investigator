from decimal import Decimal
from typing import Any

import psycopg

RULE_ID = "R6_profile_deviation"
WINDOW_DAYS = 1
FLOOR = 2
MIN_HISTORY_DAYS = 3
MIN_LOG_STDDEV = Decimal("0.25")
MIN_DAY_EUR = 1000

DELETE_SQL = "DELETE FROM core.rule_candidates WHERE rule_id = %(rule_id)s"

INSERT_SQL = """
WITH daily_out AS (
    SELECT from_account AS account_id, ts::date AS day, sum(amount_paid_eur) AS out_eur
    FROM core.transactions
    WHERE from_account <> to_account
    GROUP BY from_account, ts::date
    HAVING sum(amount_paid_eur) > 0
),
log_daily_out AS (
    SELECT account_id, day, out_eur, ln(out_eur) AS log_out
    FROM daily_out
),
profiles AS (
    SELECT account_id,
           day,
           out_eur,
           log_out,
           count(*) OVER history AS history_days,
           avg(log_out) OVER history AS mean_log_out,
           stddev_samp(log_out) OVER history AS stddev_log_out
    FROM log_daily_out
    WINDOW history AS (PARTITION BY account_id ORDER BY day ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING)
),
deviations AS (
    SELECT account_id,
           day AS alert_date,
           (day - (%(window_days)s::integer - 1))::timestamp AS window_start,
           (day + 1)::timestamp AS window_end,
           (log_out - mean_log_out) / greatest(stddev_log_out, %(min_log_stddev)s::numeric) AS z
    FROM profiles
    WHERE history_days >= %(min_history_days)s::integer
      AND out_eur >= %(min_day_eur)s::numeric
)
INSERT INTO core.rule_candidates (rule_id, account_id, alert_date, window_start, window_end, score, evidence_txn_ids)
SELECT %(rule_id)s, d.account_id, d.alert_date, d.window_start, d.window_end, d.z, evidence.txn_ids
FROM deviations d
CROSS JOIN LATERAL (
    SELECT array_agg(txn_id ORDER BY txn_id) AS txn_ids
    FROM core.transactions
    WHERE from_account = d.account_id AND to_account <> d.account_id
      AND ts >= d.window_start AND ts < d.window_end
) evidence
WHERE d.z >= %(floor)s::numeric
"""


def generate(conn: psycopg.Connection[Any]) -> int:
    parameters = {
        "rule_id": RULE_ID,
        "window_days": WINDOW_DAYS,
        "floor": FLOOR,
        "min_history_days": MIN_HISTORY_DAYS,
        "min_log_stddev": MIN_LOG_STDDEV,
        "min_day_eur": MIN_DAY_EUR,
    }
    with conn.transaction():
        conn.execute(DELETE_SQL, parameters)
        return conn.execute(INSERT_SQL, parameters).rowcount
