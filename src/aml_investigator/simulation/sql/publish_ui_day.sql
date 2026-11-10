UPDATE sim.period
SET last_day = %(day)s,
    tape_sha256 = %(hash)s,
    investigations = investigations + %(investigations)s,
    truncated = truncated OR %(truncated)s,
    cost_usd = (
        SELECT coalesce(sum(coalesce(settled_usd, reserved_usd)), 0)
        FROM sim.azure_spend
        WHERE generation = (SELECT generation FROM sim.world) AND period_id = %(period)s
    )
WHERE period_id = %(period)s
