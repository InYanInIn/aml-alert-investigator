SELECT coalesce(sum(CASE WHEN recipient.entity_id = %(client)s AND sender.entity_id <> %(client)s
                         THEN t.amount_paid_eur ELSE 0 END), 0) AS incoming,
       coalesce(sum(CASE WHEN sender.entity_id = %(client)s AND recipient.entity_id <> %(client)s
                         THEN t.amount_paid_eur ELSE 0 END), 0) AS outgoing
FROM core.transactions t
JOIN core.accounts sender ON sender.account_id = t.from_account
JOIN core.accounts recipient ON recipient.account_id = t.to_account
WHERE t.ts::date <= %(day)s AND
      (sender.entity_id = %(client)s OR recipient.entity_id = %(client)s)
