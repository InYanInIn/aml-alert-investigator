DELETE FROM core.rule_candidates c
USING core.accounts a
WHERE a.account_id = c.account_id
  AND a.entity_type <> ALL (%(entity_types)s::text[])
