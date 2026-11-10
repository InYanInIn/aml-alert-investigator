SELECT EXISTS (
    SELECT 1 FROM pg_stat_activity
    WHERE datname = current_database()
      AND application_name = 'aml-rerun:' || %(alert_id)s::text
), (
    SELECT max(investigation_id) FROM review.investigations WHERE alert_id = %(alert_id)s
)
