SELECT a.entity_id AS id, coalesce(p.display_name, a.entity_id) AS name,
       p.occupation, p.declared_monthly_eur,
       array_agg(a.account_id ORDER BY a.account_id) AS accounts,
       min(b.decided_on + 1) AS blocked_from,
       count(DISTINCT al.alert_id) AS alerts
FROM core.accounts a
JOIN core.customer_profiles p ON p.entity_id = a.entity_id
LEFT JOIN sim.block b ON b.account_id = a.account_id
LEFT JOIN core.alerts al ON al.account_id = a.account_id
WHERE a.bank_id = 1
GROUP BY a.entity_id, p.display_name, p.occupation, p.declared_monthly_eur
ORDER BY name, id
