SELECT t.txn_id, t.ts, t.from_account, t.to_account, t.amount_paid, t.paid_currency,
       t.amount_received, t.received_currency, t.payment_format, {reference} AS reference_untrusted
FROM core.transactions t
WHERE t.txn_id = ANY(%(txn_ids)s::bigint[]) AND t.ts < %(as_of)s
ORDER BY t.ts, t.txn_id
