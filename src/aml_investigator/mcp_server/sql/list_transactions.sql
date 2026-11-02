SELECT t.txn_id, t.ts, t.from_account, t.to_account, t.amount_paid, t.paid_currency,
       t.amount_received, t.received_currency, t.payment_format, {reference} AS reference_untrusted
FROM core.transactions t
WHERE t.ts >= %(lower)s AND t.ts < %(upper)s AND t.amount_paid >= %(min_amount)s::numeric
  AND ((%(outgoing)s AND t.from_account = %(account)s)
       OR (%(incoming)s AND t.to_account = %(account)s))
ORDER BY t.ts, t.txn_id
LIMIT %(fetch)s
