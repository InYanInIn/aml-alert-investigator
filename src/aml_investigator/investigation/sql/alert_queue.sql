SELECT a.alert_id,
       a.account_id,
       a.alert_date,
       coalesce(
           (SELECT string_agg(h.rule_id || ' (' || h.score || ')', ', ' ORDER BY h.rule_id)
            FROM core.alert_rule_hits h WHERE h.alert_id = a.alert_id),
           ''
       ) AS rule_hits,
       CASE
           WHEN i.investigation_id IS NULL THEN 'none'
           WHEN d.final_decision IS NULL THEN 'awaiting review'
           ELSE 'decided: ' || d.final_decision
       END AS status
FROM core.alerts a
LEFT JOIN LATERAL (
    SELECT investigation_id FROM review.investigations
    WHERE alert_id = a.alert_id ORDER BY investigation_id DESC LIMIT 1
) i ON true
LEFT JOIN review.decisions d ON d.investigation_id = i.investigation_id
ORDER BY a.alert_date DESC, a.alert_id
