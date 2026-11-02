SELECT i.alert_id, i.thread_id, i.route, d.decision_id IS NOT NULL AS decided, q.qa_review_id IS NOT NULL AS qa_reviewed
FROM review.investigations i
LEFT JOIN review.decisions d ON d.investigation_id = i.investigation_id
LEFT JOIN review.qa_reviews q ON q.investigation_id = i.investigation_id
WHERE i.investigation_id = %s
