SELECT entity_type, count(*)
FROM core.accounts
WHERE bank_id = %(monitored_bank_id)s
GROUP BY entity_type
