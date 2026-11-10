SELECT CASE i.route WHEN 'qa_sample' THEN 'qa_sample' ELSE 'escalation' END AS kind,
       i.investigation_id,
       a.alert_id,
       a.account_id,
       a.alert_date,
       coalesce(
           (SELECT string_agg(h.rule_id || ' (' || h.score || ')', ', ' ORDER BY h.rule_id)
            FROM core.alert_rule_hits h WHERE h.alert_id = a.alert_id),
           ''
       ) AS rule_hits,
       i.recommendation,
       i.verification ->> 'status' AS verification_status,
       i.created_at
FROM core.alerts a
JOIN LATERAL (
    SELECT investigation_id, route, recommendation, verification, created_at FROM review.investigations
    WHERE alert_id = a.alert_id ORDER BY investigation_id DESC LIMIT 1
) i ON true
LEFT JOIN review.decisions d ON d.investigation_id = i.investigation_id
LEFT JOIN review.qa_reviews q ON q.investigation_id = i.investigation_id
LEFT JOIN sim.alert_period ap ON ap.alert_id = a.alert_id
WHERE ((i.route = 'escalated' AND d.decision_id IS NULL) OR (i.route = 'qa_sample' AND q.qa_review_id IS NULL))
  AND (%(period)s::bigint IS NULL OR ap.period_id = %(period)s)
ORDER BY i.investigation_id
