COPY (
    SELECT txn_id, from_account, to_account, ts, amount_paid_eur
    FROM core.transactions
    WHERE from_account <> to_account AND amount_paid_eur >= %(min_hop_eur)s
) TO STDOUT (FORMAT csv, HEADER)
