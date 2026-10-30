SELECT i.alert_id, i.thread_id, d.decision_id IS NOT NULL AS decided
FROM review.investigations i
LEFT JOIN review.decisions d ON d.investigation_id = i.investigation_id
WHERE i.investigation_id = %s
