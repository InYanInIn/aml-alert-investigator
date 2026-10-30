laundering_sides AS (
    SELECT t.txn_id, t.ts, side.account_id
    FROM core.transactions t
    JOIN truth.transaction_labels l USING (txn_id)
    CROSS JOIN LATERAL (VALUES (t.from_account), (t.to_account)) AS side(account_id)
    WHERE l.is_laundering
),
