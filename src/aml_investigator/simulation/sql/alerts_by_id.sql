SELECT alert_id, account_id, alert_date
FROM core.alerts
WHERE alert_id = ANY(%(alert_ids)s)
ORDER BY alert_id
