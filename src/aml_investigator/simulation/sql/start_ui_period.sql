WITH selected_system AS (
    UPDATE sim.world SET system = %(system)s
)
INSERT INTO sim.period (first_day, last_day, target_day, system, investigation_cap, status, tape_sha256)
VALUES (%(first)s, %(last)s, %(target)s, %(system)s, %(cap)s, 'running', %(hash)s)
RETURNING period_id, first_day, last_day, target_day, system, investigation_cap, cost_usd,
          investigations, status, tape_sha256, truncated, error
