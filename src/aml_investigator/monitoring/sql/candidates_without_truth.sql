SELECT count(*)
FROM core.rule_candidates c
LEFT JOIN truth.candidate_truth t USING (rule_id, account_id, alert_date)
WHERE t.rule_id IS NULL
