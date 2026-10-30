WITH thresholds AS (
    SELECT rule_id, threshold
    FROM unnest(%(rule_ids)s::text[], %(limits)s::numeric[]) AS t(rule_id, threshold)
),
qualifying AS (
    SELECT c.*
    FROM core.rule_candidates c
    JOIN thresholds t ON t.rule_id = c.rule_id AND c.score >= t.threshold
    WHERE c.alert_date BETWEEN %(first_day)s::date - %(suppression_days)s::integer AND %(last_day)s::date
),
first_days AS (
    SELECT q.*
    FROM qualifying q
    WHERE q.alert_date >= %(first_day)s::date
      AND NOT EXISTS (
        SELECT 1
        FROM qualifying earlier
        WHERE earlier.rule_id = q.rule_id
          AND earlier.account_id = q.account_id
          AND earlier.alert_date BETWEEN q.alert_date - %(suppression_days)s::integer AND q.alert_date - 1
    )
),
account_days AS (
    SELECT account_id, alert_date, min(window_start) AS window_start, max(window_end) AS window_end
    FROM first_days
    GROUP BY account_id, alert_date
),
