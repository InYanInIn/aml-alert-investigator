SELECT EXISTS (
    SELECT 1 FROM sim.azure_spend WHERE generation = %s AND account_id = %s AND alert_date = %s
)
