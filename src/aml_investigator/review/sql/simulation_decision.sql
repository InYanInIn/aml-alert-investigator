SELECT d.recommendation, d.skip_reason, p.system
FROM sim.decision d
LEFT JOIN sim.alert_period ap USING (alert_id)
LEFT JOIN sim.period p USING (period_id)
WHERE d.alert_id = %s
