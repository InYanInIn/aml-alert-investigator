SELECT
    schemaname || '.' || tablename AS table_name,
    (xpath(
        '/row/n/text()',
        query_to_xml(format('SELECT count(*) AS n FROM %I.%I', schemaname, tablename), false, true, '')
    ))[1]::text::bigint AS row_count
FROM pg_tables
WHERE schemaname IN ('core', 'truth', 'review', 'sim', 'corpus')
ORDER BY 1;
