SELECT i.investigation_id, i.recommendation, i.decision, i.verification, i.created_at,
       d.final_decision, d.analyst, d.comment, d.decided_at
FROM review.investigations i
LEFT JOIN review.decisions d ON d.investigation_id = i.investigation_id
WHERE i.alert_id = %s
ORDER BY i.investigation_id DESC
LIMIT 1
