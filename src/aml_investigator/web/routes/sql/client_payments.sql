SELECT t.txn_id, t.ts, t.from_account, t.to_account, t.amount_paid_eur AS eur,
       t.payment_format AS category, t.reference
FROM core.transactions t
WHERE EXISTS (
    SELECT 1 FROM core.accounts a
    WHERE a.account_id IN (t.from_account, t.to_account)
      AND a.entity_id = %(client)s AND a.bank_id = 1
) AND t.ts::date <= %(day)s
ORDER BY t.ts DESC, t.txn_id DESC LIMIT 40
