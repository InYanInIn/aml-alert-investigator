WITH flows AS (
    SELECT from_account AS account_id, ts::date AS day, amount_paid_eur, true AS outgoing
    FROM core.transactions
    WHERE from_account <> to_account
    UNION ALL
    SELECT to_account, ts::date, amount_paid_eur, false
    FROM core.transactions
    WHERE from_account <> to_account
),
daily AS (
    SELECT account_id,
           day,
           coalesce(sum(amount_paid_eur) FILTER (WHERE NOT outgoing), 0) AS in_eur,
           coalesce(sum(amount_paid_eur) FILTER (WHERE outgoing), 0) AS out_eur,
           bool_or(outgoing) AS sends
    FROM flows
    GROUP BY account_id, day
),
windows AS (
    SELECT d.account_id, d.day AS alert_date, sum(w.in_eur) AS in_eur, sum(w.out_eur) AS out_eur
    FROM daily d
    JOIN daily w ON w.account_id = d.account_id
                AND w.day BETWEEN d.day - (%(window_days)s::integer - 1) AND d.day
    WHERE d.sends
    GROUP BY d.account_id, d.day
),
pass_throughs AS (
    SELECT account_id,
           alert_date,
           (alert_date - (%(window_days)s::integer - 1))::timestamp AS window_start,
           (alert_date + 1)::timestamp AS window_end,
           least(in_eur, out_eur) AS passed_eur
    FROM windows
    WHERE out_eur >= %(ratio_lower)s::numeric * in_eur
      AND out_eur <= %(ratio_upper)s::numeric * in_eur
      AND least(in_eur, out_eur) >= %(floor)s::numeric
)
INSERT INTO core.rule_candidates (rule_id, account_id, alert_date, window_start, window_end, score, evidence_txn_ids)
SELECT %(rule_id)s, p.account_id, p.alert_date, p.window_start, p.window_end, p.passed_eur, evidence.txn_ids
FROM pass_throughs p
CROSS JOIN LATERAL (
    SELECT array_agg(txn_id ORDER BY txn_id) AS txn_ids
    FROM (
        SELECT txn_id
        FROM core.transactions
        WHERE from_account = p.account_id AND to_account <> p.account_id
          AND ts >= p.window_start AND ts < p.window_end
        UNION ALL
        SELECT txn_id
        FROM core.transactions
        WHERE to_account = p.account_id AND from_account <> p.account_id
          AND ts >= p.window_start AND ts < p.window_end
    ) window_transactions
) evidence
