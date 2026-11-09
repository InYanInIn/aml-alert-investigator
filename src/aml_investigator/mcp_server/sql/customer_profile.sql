SELECT p.display_name,
       p.occupation,
       p.business_description,
       p.account_purpose,
       p.expected_activity,
       p.declared_monthly_eur,
       p.customer_since
FROM core.customer_profiles p
JOIN core.accounts a USING (entity_id)
WHERE a.account_id = %(account)s
