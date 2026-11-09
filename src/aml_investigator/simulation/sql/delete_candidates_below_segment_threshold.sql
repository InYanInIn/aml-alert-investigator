DELETE FROM core.rule_candidates c
USING core.accounts a
WHERE a.account_id = c.account_id
  AND NOT EXISTS (
      SELECT 1
      FROM unnest(%(entity_types)s::text[], %(rule_ids)s::text[], %(limits)s::numeric[]) AS t(entity_type, rule_id, threshold)
      WHERE t.entity_type = a.entity_type AND t.rule_id = c.rule_id AND c.score >= t.threshold
  )
