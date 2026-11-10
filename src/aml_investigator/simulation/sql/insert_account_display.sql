INSERT INTO sim.account_display (account_id, opening_eur, external_kind)
SELECT account_id, opening_eur, external_kind
FROM unnest(%s::text[], %s::numeric[], %s::text[]) AS accounts(account_id, opening_eur, external_kind)
