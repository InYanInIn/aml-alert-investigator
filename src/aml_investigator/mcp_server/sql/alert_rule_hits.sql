SELECT rule_id, score, evidence_txn_ids FROM core.alert_rule_hits WHERE alert_id = %s ORDER BY rule_id
