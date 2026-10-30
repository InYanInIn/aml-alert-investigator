alert_counts AS (
    SELECT count(*) AS alerts,
           count(*) FILTER (WHERE EXISTS (
               SELECT FROM laundering_sides l
               WHERE l.account_id = a.account_id AND l.ts >= a.window_start AND l.ts < a.window_end
           )) AS positive_alerts
    FROM account_days a
)
SELECT alerts, positive_alerts FROM alert_counts
