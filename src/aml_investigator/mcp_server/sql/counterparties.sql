WITH flows AS (
    SELECT to_account AS counterparty, 'out' AS direction, ts, amount_paid_eur, payment_format
    FROM core.transactions
    WHERE %(outgoing)s AND from_account = %(account)s AND to_account <> %(account)s
      AND ts >= %(lower)s AND ts < %(upper)s
    UNION ALL
    SELECT from_account, 'in', ts, amount_paid_eur, payment_format
    FROM core.transactions
    WHERE %(incoming)s AND to_account = %(account)s AND from_account <> %(account)s
      AND ts >= %(lower)s AND ts < %(upper)s
),
ranked AS (
    SELECT counterparty AS account_id,
           count(*) FILTER (WHERE direction = 'in') AS incoming_count,
           coalesce(sum(amount_paid_eur) FILTER (WHERE direction = 'in'), 0) AS incoming_eur,
           count(*) FILTER (WHERE direction = 'out') AS outgoing_count,
           coalesce(sum(amount_paid_eur) FILTER (WHERE direction = 'out'), 0) AS outgoing_eur,
           sum(amount_paid_eur) AS total_eur,
           min(ts) AS first_ts,
           max(ts) AS last_ts,
           array_agg(DISTINCT payment_format ORDER BY payment_format) AS payment_formats,
           count(*) OVER () AS counterparty_count
    FROM flows
    GROUP BY counterparty
)
SELECT *
FROM ranked
ORDER BY total_eur DESC, account_id
LIMIT %(limit)s
