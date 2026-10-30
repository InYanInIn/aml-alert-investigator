touched AS (
    SELECT DISTINCT f.rule_id, f.account_id, f.alert_date, l.txn_id
    FROM first_days f
    JOIN laundering_sides l ON l.account_id = f.account_id AND l.ts >= f.window_start AND l.ts < f.window_end
),
window_attempts AS (
    SELECT DISTINCT p.attempt_id
    FROM truth.pattern_attempts p
    JOIN core.transactions t USING (txn_id)
    WHERE t.ts >= %(first_day)s::date AND t.ts < %(last_day)s::date + 1
),
touched_attempts AS (
    SELECT DISTINCT t.rule_id, p.attempt_id
    FROM touched t
    JOIN truth.pattern_attempts p USING (txn_id)
    JOIN window_attempts USING (attempt_id)
)
SELECT th.rule_id,
       th.threshold,
       (SELECT count(*) FROM first_days f WHERE f.rule_id = th.rule_id),
       (SELECT count(DISTINCT (t.account_id, t.alert_date)) FROM touched t WHERE t.rule_id = th.rule_id),
       (SELECT count(*) FROM touched_attempts a WHERE a.rule_id = th.rule_id),
       (SELECT count(*) FROM window_attempts)
FROM thresholds th
