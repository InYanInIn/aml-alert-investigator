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
