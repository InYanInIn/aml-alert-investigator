SELECT
    count(*),
    count(*) FILTER (WHERE escalate_truth),
    count(*) FILTER (WHERE escalate_truth_account_level),
    count(*) FILTER (WHERE cardinality(typologies) > 0)
FROM truth.alert_truth
