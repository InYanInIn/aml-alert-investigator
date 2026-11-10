SELECT ((t.ts::date - DATE '2025-03-03') / 7) + 1 AS week,
       t.payment_format AS category, sum(t.amount_paid_eur) AS eur
FROM core.transactions t
JOIN core.accounts a ON a.account_id = t.from_account
JOIN core.accounts recipient ON recipient.account_id = t.to_account
WHERE a.entity_id = %(client)s AND a.bank_id = 1 AND recipient.entity_id <> %(client)s
      AND t.ts::date <= %(day)s
GROUP BY week, category ORDER BY week, category
