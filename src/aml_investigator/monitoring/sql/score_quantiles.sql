SELECT rule_id, percentile_disc(%(quantiles)s::float8[]) WITHIN GROUP (ORDER BY score)
FROM core.rule_candidates
WHERE rule_id = ANY (%(rule_ids)s::text[]) AND alert_date BETWEEN %(first_day)s::date AND %(last_day)s::date
GROUP BY rule_id
