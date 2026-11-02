SELECT a.account_id, a.alert_date, a.window_start, a.window_end, t.escalate_truth,
       target.txn_id, target.ts, target.from_account, target.to_account
FROM core.alerts a
JOIN truth.alert_truth t USING (alert_id)
CROSS JOIN LATERAL (
    SELECT txn_id, ts, from_account, to_account
    FROM core.transactions
    WHERE ts >= a.window_start AND ts < a.window_end
      AND (from_account = a.account_id OR to_account = a.account_id)
    ORDER BY ts, txn_id
    LIMIT 1
) target
WHERE a.alert_date BETWEEN %(first)s AND %(last)s
ORDER BY a.account_id, a.alert_date
