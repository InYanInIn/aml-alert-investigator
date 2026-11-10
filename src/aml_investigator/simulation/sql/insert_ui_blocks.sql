INSERT INTO sim.block (account_id, decided_on, period_id)
SELECT account_id, %s, %s
FROM unnest(%s::text[]) AS account_id
