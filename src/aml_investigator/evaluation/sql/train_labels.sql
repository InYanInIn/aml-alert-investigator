SELECT a.alert_id, t.escalate_truth
FROM core.alerts a
JOIN truth.alert_truth t USING (alert_id)
WHERE a.alert_date BETWEEN %(first_day)s AND %(last_day)s
ORDER BY a.alert_id
