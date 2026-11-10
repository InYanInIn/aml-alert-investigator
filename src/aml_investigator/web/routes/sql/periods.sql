SELECT period_id, first_day, last_day, target_day, system, status, truncated,
       cost_usd, investigations
FROM sim.period ORDER BY period_id
