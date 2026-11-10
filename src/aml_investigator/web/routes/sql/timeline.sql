WITH payments AS (
    SELECT ((ts::date - DATE '2025-03-03') / 7) + 1 AS week,
           count(*) AS payments, sum(amount_paid_eur) AS eur
    FROM core.transactions GROUP BY week
), alerts AS (
    SELECT ((alert_date - DATE '2025-03-03') / 7) + 1 AS week, count(*) AS alerts
    FROM core.alerts GROUP BY week
), blocks AS (
    SELECT ((decided_on - DATE '2025-03-03') / 7) + 1 AS week, count(*) AS blocks
    FROM sim.block GROUP BY week
)
SELECT p.week, p.payments, p.eur, coalesce(a.alerts, 0) AS alerts,
       coalesce(b.blocks, 0) AS blocks
FROM payments p LEFT JOIN alerts a USING (week) LEFT JOIN blocks b USING (week)
ORDER BY p.week
