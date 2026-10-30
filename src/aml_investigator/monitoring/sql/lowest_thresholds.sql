WITH scored AS (
    SELECT rule_id,
           alert_date,
           score,
           max(score) OVER (
               PARTITION BY rule_id, account_id
               ORDER BY alert_date
               RANGE BETWEEN make_interval(days => %(suppression_days)s::integer) PRECEDING
                         AND '1 day'::interval PRECEDING
           ) AS suppressing_score
    FROM core.rule_candidates
    WHERE rule_id = ANY (%(rule_ids)s::text[])
      AND alert_date BETWEEN %(first_day)s::date - %(suppression_days)s::integer AND %(last_day)s::date
),
calibration_days AS (
    SELECT * FROM scored WHERE alert_date >= %(first_day)s::date
),
steps AS (
    SELECT rule_id, score AS level, 1 AS alerts_delta, true AS is_score
    FROM calibration_days
    UNION ALL
    SELECT rule_id, least(suppressing_score, score), -1, false
    FROM calibration_days
    WHERE suppressing_score IS NOT NULL
),
levels AS (
    SELECT rule_id, level, sum(alerts_delta) AS alerts_delta, bool_or(is_score) AS is_score
    FROM steps
    GROUP BY rule_id, level
),
volume AS (
    SELECT rule_id, level, is_score, sum(alerts_delta) OVER (PARTITION BY rule_id ORDER BY level DESC) AS alerts
    FROM levels
)
SELECT rule_id,
       max(level) FILTER (WHERE is_score),
       min(level) FILTER (WHERE is_score AND alerts <= %(max_alerts)s::float8)
FROM volume
GROUP BY rule_id
