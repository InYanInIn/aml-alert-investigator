SELECT a.account_id, a.alert_date, t.escalate_truth
FROM core.alerts a
LEFT JOIN truth.alert_truth t USING (alert_id)
WHERE a.alert_date BETWEEN %(first)s AND %(last)s
ORDER BY a.account_id, a.alert_date
