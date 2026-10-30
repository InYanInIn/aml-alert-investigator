WITH laundering AS (
    SELECT t.txn_id, t.ts, t.from_account, t.to_account
    FROM core.transactions t
    JOIN truth.transaction_labels l USING (txn_id)
    WHERE l.is_laundering
),
laundering_accounts AS (
    SELECT from_account AS account_id FROM laundering
    UNION
    SELECT to_account FROM laundering
),
in_window AS (
    SELECT a.alert_id, l.txn_id
    FROM core.alerts a
    JOIN laundering l
      ON a.account_id IN (l.from_account, l.to_account)
     AND l.ts >= a.window_start
     AND l.ts < a.window_end
)
INSERT INTO truth.alert_truth (alert_id, escalate_truth, escalate_truth_account_level, typologies)
SELECT
    a.alert_id,
    EXISTS (SELECT FROM in_window w WHERE w.alert_id = a.alert_id),
    EXISTS (SELECT FROM laundering_accounts la WHERE la.account_id = a.account_id),
    coalesce(
        (
            SELECT array_agg(DISTINCT p.typology ORDER BY p.typology)
            FROM in_window w
            JOIN truth.pattern_attempts p USING (txn_id)
            WHERE w.alert_id = a.alert_id
        ),
        '{}'
    )
FROM core.alerts a
