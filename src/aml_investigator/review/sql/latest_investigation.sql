SELECT i.investigation_id, i.recommendation, i.route, i.decision, i.verification, i.trace, i.created_at,
       d.final_decision, d.analyst, d.comment, d.decided_at,
       q.outcome AS qa_outcome, q.analyst AS qa_analyst, q.comment AS qa_comment, q.reviewed_at AS qa_reviewed_at
FROM review.investigations i
LEFT JOIN review.decisions d ON d.investigation_id = i.investigation_id
LEFT JOIN review.qa_reviews q ON q.investigation_id = i.investigation_id
WHERE i.alert_id = %s
ORDER BY i.investigation_id DESC
LIMIT 1
