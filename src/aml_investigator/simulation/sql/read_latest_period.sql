SELECT period_id, first_day, last_day, target_day, system, investigation_cap, cost_usd,
       investigations, status, tape_sha256, truncated, error
FROM sim.period
ORDER BY period_id DESC
LIMIT 1
