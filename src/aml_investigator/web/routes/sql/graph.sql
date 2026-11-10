SELECT CASE WHEN sender.bank_id = 1 THEN sender.entity_id
            ELSE 'external:' || coalesce(sd.external_kind, 'other_external') END AS from_client,
       CASE WHEN recipient.bank_id = 1 THEN recipient.entity_id
            ELSE 'external:' || coalesce(rd.external_kind, 'other_external') END AS to_client,
       count(*) AS count, sum(t.amount_paid_eur) AS eur
FROM core.transactions t
JOIN core.accounts sender ON sender.account_id = t.from_account
JOIN core.accounts recipient ON recipient.account_id = t.to_account
LEFT JOIN sim.account_display sd ON sd.account_id = sender.account_id
LEFT JOIN sim.account_display rd ON rd.account_id = recipient.account_id
WHERE t.ts::date BETWEEN %(start)s AND %(end)s
GROUP BY from_client, to_client ORDER BY from_client, to_client
