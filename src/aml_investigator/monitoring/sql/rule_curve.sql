window_attempts AS (
    SELECT DISTINCT p.attempt_id
    FROM truth.pattern_attempts p
    JOIN core.transactions t USING (txn_id)
    WHERE t.ts >= %(first_day)s::date AND t.ts < %(last_day)s::date + 1
),
positive_days AS (
    SELECT f.rule_id, f.account_id, f.alert_date, f.window_start, f.window_end
    FROM first_days f
    JOIN truth.candidate_truth ct USING (rule_id, account_id, alert_date)
    WHERE ct.has_laundering
),
touched_attempts AS (
    SELECT DISTINCT f.rule_id, p.attempt_id
    FROM truth.pattern_attempts p
    JOIN window_attempts w ON w.attempt_id = p.attempt_id
    JOIN truth.transaction_labels l ON l.txn_id = p.txn_id
    JOIN core.transactions t ON t.txn_id = p.txn_id
    CROSS JOIN LATERAL (VALUES (t.from_account), (t.to_account)) AS side(account_id)
    JOIN positive_days f ON f.account_id = side.account_id AND t.ts >= f.window_start AND t.ts < f.window_end
    WHERE l.is_laundering
),
positives AS (
    SELECT rule_id, count(*) AS positive_alerts
    FROM positive_days
    GROUP BY rule_id
)
SELECT th.rule_id,
       th.threshold,
       (SELECT count(*) FROM first_days f WHERE f.rule_id = th.rule_id),
       coalesce((SELECT p.positive_alerts FROM positives p WHERE p.rule_id = th.rule_id), 0),
       (SELECT count(*) FROM touched_attempts a WHERE a.rule_id = th.rule_id),
       (SELECT count(*) FROM window_attempts)
FROM thresholds th
