WITH history AS (
    SELECT txn_id, ts, from_account, to_account, amount_paid_eur, payment_format,
           from_account = %(account)s AS outgoing, to_account = %(account)s AS incoming
    FROM core.transactions
    WHERE from_account = %(account)s AND ts < %(as_of)s
    UNION ALL
    SELECT txn_id, ts, from_account, to_account, amount_paid_eur, payment_format, false, true
    FROM core.transactions
    WHERE to_account = %(account)s AND from_account <> %(account)s AND ts < %(as_of)s
),
daily_out AS (
    SELECT ts::date AS day, sum(amount_paid_eur) AS out_eur
    FROM history
    WHERE outgoing AND to_account <> %(account)s AND ts < %(window_start)s
    GROUP BY ts::date
),
formats AS (
    SELECT coalesce(jsonb_agg(jsonb_build_object('payment_format', payment_format, 'share', share)
                              ORDER BY share DESC, payment_format), '[]'::jsonb) AS payment_format_shares
    FROM (
        SELECT payment_format, round(count(*)::numeric / sum(count(*)) OVER (), 4) AS share
        FROM history
        GROUP BY payment_format
    ) f
)
SELECT a.account_id,
       a.entity_type,
       stats.first_seen,
       stats.active_days,
       stats.incoming_count,
       stats.incoming_eur,
       stats.outgoing_count,
       stats.outgoing_eur,
       stats.self_transfer_count,
       stats.distinct_counterparties,
       formats.payment_format_shares,
       baseline.outgoing_days,
       baseline.daily_outgoing_eur_mean,
       baseline.daily_outgoing_eur_median,
       siblings.other_account_count,
       coalesce(siblings.other_account_ids, ARRAY[]::text[]) AS other_account_ids
FROM core.accounts a
CROSS JOIN LATERAL (
    SELECT min(ts) AS first_seen,
           count(DISTINCT ts::date) AS active_days,
           count(*) FILTER (WHERE incoming AND NOT outgoing) AS incoming_count,
           coalesce(sum(amount_paid_eur) FILTER (WHERE incoming AND NOT outgoing), 0) AS incoming_eur,
           count(*) FILTER (WHERE outgoing AND NOT incoming) AS outgoing_count,
           coalesce(sum(amount_paid_eur) FILTER (WHERE outgoing AND NOT incoming), 0) AS outgoing_eur,
           count(*) FILTER (WHERE outgoing AND incoming) AS self_transfer_count,
           count(DISTINCT CASE WHEN outgoing THEN to_account ELSE from_account END)
               FILTER (WHERE NOT (outgoing AND incoming)) AS distinct_counterparties
    FROM history
) stats
CROSS JOIN formats
CROSS JOIN LATERAL (
    SELECT count(*) AS outgoing_days,
           avg(out_eur) AS daily_outgoing_eur_mean,
           percentile_cont(0.5) WITHIN GROUP (ORDER BY out_eur) AS daily_outgoing_eur_median
    FROM daily_out
) baseline
CROSS JOIN LATERAL (
    SELECT count(*) AS other_account_count,
           (array_agg(o.account_id ORDER BY o.account_id))[1:10] AS other_account_ids
    FROM core.accounts o
    WHERE o.entity_id = a.entity_id AND o.account_id <> a.account_id
) siblings
WHERE a.account_id = %(account)s
