UPDATE sim.azure_spend
SET settled_usd = %s
WHERE generation = %s AND account_id = %s AND alert_date = %s AND ordinal = %s
