SELECT i.investigation_id,
       a.alert_id,
       a.account_id,
       a.alert_date,
       i.route,
       CASE
           WHEN i.route = 'auto_closed' THEN 'not_sampled'
           WHEN q.outcome IS NULL THEN 'pending'
           ELSE q.outcome
       END AS qa_status,
       d.decided_at AS closed_at
FROM core.alerts a
JOIN LATERAL (
    SELECT investigation_id, route FROM review.investigations
    WHERE alert_id = a.alert_id ORDER BY investigation_id DESC LIMIT 1
) i ON true
JOIN review.decisions d ON d.investigation_id = i.investigation_id
LEFT JOIN review.qa_reviews q ON q.investigation_id = i.investigation_id
WHERE i.route IN ('auto_closed', 'qa_sample')
ORDER BY d.decided_at DESC, i.investigation_id DESC
