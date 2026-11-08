WITH alert AS (
    SELECT a.account_id, a.window_start, a.window_end, acc.entity_id
    FROM core.alerts a
    JOIN core.accounts acc USING (account_id)
    WHERE a.alert_id = %(alert_id)s
),
window_payments AS (
    SELECT t.to_account, t.paid_currency, t.received_currency
    FROM alert
    JOIN core.transactions t ON t.from_account = alert.account_id
        AND t.ts >= alert.window_start AND t.ts < alert.window_end
    UNION ALL
    SELECT t.to_account, t.paid_currency, t.received_currency
    FROM alert
    JOIN core.transactions t ON t.to_account = alert.account_id AND t.from_account <> alert.account_id
        AND t.ts >= alert.window_start AND t.ts < alert.window_end
)
SELECT NOT EXISTS (SELECT FROM window_payments WHERE to_account = alert.account_id)
       AND NOT EXISTS (SELECT FROM window_payments WHERE paid_currency <> received_currency)
       AND (SELECT count(*) FROM core.accounts o
            WHERE o.entity_id = alert.entity_id AND o.account_id <> alert.account_id) >= 3 AS spends_own_money
FROM alert
