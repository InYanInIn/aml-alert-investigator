SELECT alert_id, escalate_truth, typologies
FROM truth.alert_truth
WHERE alert_id = ANY(%(alert_ids)s)
