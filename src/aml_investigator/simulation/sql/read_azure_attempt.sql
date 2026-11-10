SELECT COALESCE(max(ordinal), 0)
FROM sim.azure_spend
WHERE generation = (SELECT generation FROM sim.world) AND account_id = %s AND alert_date = %s
