SELECT a.alert_id, a.account_id, a.alert_date
FROM core.alerts a
JOIN unnest(%(account_ids)s::text[], %(alert_dates)s::date[]) AS k (account_id, alert_date)
    USING (account_id, alert_date)
ORDER BY a.account_id, a.alert_date
