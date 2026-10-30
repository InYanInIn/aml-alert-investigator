WITH incoming AS (
    SELECT txn_id, to_account AS account_id, from_account, ts
    FROM core.transactions
    WHERE from_account <> to_account
),
active_days AS (
    SELECT DISTINCT account_id, ts::date AS alert_date
    FROM incoming
),
windows AS (
    SELECT account_id,
           alert_date,
           (alert_date - (%(window_days)s::integer - 1))::timestamp AS window_start,
           (alert_date + 1)::timestamp AS window_end
    FROM active_days
)
INSERT INTO core.rule_candidates (rule_id, account_id, alert_date, window_start, window_end, score, evidence_txn_ids)
SELECT %(rule_id)s,
       w.account_id,
       w.alert_date,
       w.window_start,
       w.window_end,
       count(DISTINCT i.from_account),
       array_agg(i.txn_id ORDER BY i.txn_id)
FROM windows w
JOIN incoming i ON i.account_id = w.account_id AND i.ts >= w.window_start AND i.ts < w.window_end
GROUP BY w.account_id, w.alert_date, w.window_start, w.window_end
HAVING count(DISTINCT i.from_account) >= %(floor)s::integer
