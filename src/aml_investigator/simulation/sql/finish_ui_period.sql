UPDATE sim.period
SET status = CASE WHEN %(error)s::text IS NULL THEN 'done' ELSE 'failed' END,
    finished_at = now(),
    error = %(error)s,
    cost_usd = (
        SELECT coalesce(sum(coalesce(settled_usd, reserved_usd)), 0)
        FROM sim.azure_spend AS spend
        WHERE spend.generation = (SELECT generation FROM sim.world) AND spend.period_id = sim.period.period_id
    )
WHERE status = 'running'
