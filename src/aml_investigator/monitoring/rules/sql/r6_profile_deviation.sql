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
