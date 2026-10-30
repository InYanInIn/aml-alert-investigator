WITH laundering_sides AS (
    SELECT DISTINCT side.account_id, t.ts
    FROM core.transactions t
    JOIN truth.transaction_labels l USING (txn_id)
    CROSS JOIN LATERAL (VALUES (t.from_account), (t.to_account)) AS side(account_id)
    WHERE l.is_laundering
),
positive AS (
    SELECT DISTINCT c.rule_id, c.account_id, c.alert_date
    FROM core.rule_candidates c
    JOIN laundering_sides l
      ON l.account_id = c.account_id
     AND l.ts >= c.window_start
     AND l.ts < c.window_end
)
INSERT INTO truth.candidate_truth (rule_id, account_id, alert_date, has_laundering)
SELECT c.rule_id, c.account_id, c.alert_date, p.rule_id IS NOT NULL
FROM core.rule_candidates c
LEFT JOIN positive p USING (rule_id, account_id, alert_date)
