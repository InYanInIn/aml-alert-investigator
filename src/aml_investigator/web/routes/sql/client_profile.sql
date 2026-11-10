SELECT a.entity_id AS id, coalesce(p.display_name, a.entity_id) AS name,
       p.occupation, p.business_description AS description, p.account_purpose AS purpose,
       p.expected_activity, p.declared_monthly_eur, p.customer_since AS since,
       array_agg(a.account_id ORDER BY a.account_id) AS accounts,
       sum(d.opening_eur) AS opening_eur,
       min(b.decided_on + 1) AS blocked_from
FROM core.accounts a
JOIN core.customer_profiles p ON p.entity_id = a.entity_id
JOIN sim.account_display d ON d.account_id = a.account_id
LEFT JOIN sim.block b ON b.account_id = a.account_id
WHERE a.bank_id = 1 AND a.entity_id = %(client)s
GROUP BY a.entity_id, p.display_name, p.occupation, p.business_description,
         p.account_purpose, p.expected_activity, p.declared_monthly_eur, p.customer_since
