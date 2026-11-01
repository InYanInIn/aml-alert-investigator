SELECT rule_id, score
FROM core.alert_rule_hits
WHERE alert_id = %(alert_id)s
