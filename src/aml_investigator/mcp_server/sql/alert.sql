SELECT alert_id, account_id, alert_date, window_start, window_end, window_end AS as_of
FROM core.alerts
WHERE alert_id = %s
