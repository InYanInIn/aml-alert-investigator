INSERT INTO sim.alert_period (alert_id, period_id)
SELECT alert_id, %s
FROM unnest(%s::bigint[]) AS alert_id
