SELECT a.alert_id
FROM core.alerts a
WHERE a.alert_date = %(day)s
  AND NOT EXISTS (SELECT FROM review.investigations i WHERE i.alert_id = a.alert_id)
ORDER BY a.alert_id
LIMIT %(limit)s
