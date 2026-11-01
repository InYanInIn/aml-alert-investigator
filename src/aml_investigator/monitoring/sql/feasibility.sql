positive_alerts AS (
    SELECT DISTINCT f.account_id, f.alert_date
    FROM first_days f
    JOIN truth.candidate_truth ct USING (rule_id, account_id, alert_date)
    WHERE ct.has_laundering
)
SELECT (SELECT count(*) FROM account_days), (SELECT count(*) FROM positive_alerts)
