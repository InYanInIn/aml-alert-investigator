SELECT txn_id, ts, from_account, to_account, amount_paid, paid_currency,
       amount_received, received_currency, payment_format
FROM core.transactions
WHERE ts >= %(lower)s AND ts < %(upper)s AND amount_paid >= %(min_amount)s::numeric
  AND ((%(outgoing)s AND from_account = %(account)s)
       OR (%(incoming)s AND to_account = %(account)s))
ORDER BY ts, txn_id
LIMIT %(fetch)s
