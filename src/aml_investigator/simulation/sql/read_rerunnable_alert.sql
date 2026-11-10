SELECT a.alert_id, a.account_id, a.alert_date, p.period_id
FROM core.alerts a
JOIN sim.alert_period ap USING (alert_id)
JOIN sim.period p USING (period_id)
JOIN LATERAL (
    SELECT i.investigation_id
    FROM review.investigations i
    WHERE i.alert_id = a.alert_id
    ORDER BY i.investigation_id DESC
    LIMIT 1
) latest ON true
WHERE a.alert_id = %s AND latest.investigation_id = %s
  AND p.system = 'azure_profile' AND a.alert_date <= p.last_day
