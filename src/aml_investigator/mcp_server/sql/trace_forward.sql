WITH RECURSIVE paths AS (
    SELECT 1 AS hops, first.to_account AS frontier, first.ts AS frontier_ts,
           ARRAY[first.txn_id] AS txn_ids, first.amount_paid_eur AS flow_eur
    FROM (
        SELECT txn_id, to_account, ts, amount_paid_eur
        FROM core.transactions
        WHERE from_account = %(account)s AND to_account <> %(account)s
          AND ts >= %(lower)s AND ts < %(upper)s
        ORDER BY amount_paid_eur DESC, txn_id
        LIMIT %(first_branching)s
    ) first
    UNION ALL
    SELECT p.hops + 1, next.to_account, next.ts, p.txn_ids || next.txn_id, least(p.flow_eur, next.amount_paid_eur)
    FROM paths p
    CROSS JOIN LATERAL (
        SELECT txn_id, to_account, ts, amount_paid_eur
        FROM core.transactions
        WHERE from_account = p.frontier AND to_account <> p.frontier
          AND ts >= p.frontier_ts AND ts < %(upper)s
          AND txn_id <> ALL(p.txn_ids)
        ORDER BY amount_paid_eur DESC, txn_id
        LIMIT %(branching)s
    ) next
    WHERE p.hops < %(max_hops)s
),
terminal AS (
    SELECT p.txn_ids, p.flow_eur
    FROM paths p
    WHERE NOT EXISTS (
        SELECT FROM paths longer
        WHERE longer.hops = p.hops + 1 AND longer.txn_ids[1:p.hops] = p.txn_ids
    )
)
SELECT terminal.flow_eur,
       count(*) OVER () AS path_count,
       hops.hops
FROM terminal
CROSS JOIN LATERAL (
    SELECT jsonb_agg(jsonb_build_object('txn_id', t.txn_id, 'ts', t.ts, 'from_account', t.from_account,
                                        'to_account', t.to_account, 'amount_eur', t.amount_paid_eur)
                     ORDER BY hop.position) AS hops
    FROM unnest(terminal.txn_ids) WITH ORDINALITY AS hop(txn_id, position)
    JOIN core.transactions t ON t.txn_id = hop.txn_id
) hops
ORDER BY terminal.flow_eur DESC, terminal.txn_ids
LIMIT %(limit)s
