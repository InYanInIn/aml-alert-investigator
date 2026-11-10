SELECT al.alert_id, al.alert_date, al.account_id FROM core.alerts al
JOIN core.accounts a ON a.account_id = al.account_id
WHERE a.entity_id = %(client)s AND a.bank_id = 1 AND al.alert_date <= %(day)s
ORDER BY al.alert_date DESC, al.alert_id DESC
