SELECT coalesce(sum(coalesce(settled_usd, reserved_usd)), 0)
FROM sim.azure_spend
