SELECT n.nspname, c.relname, array_agg(a.attname ORDER BY a.attnum)
FROM pg_class c
JOIN pg_namespace n ON n.oid = c.relnamespace
JOIN pg_attribute a ON a.attrelid = c.oid AND a.attnum > 0 AND NOT a.attisdropped AND a.attgenerated = ''
WHERE c.relkind = 'r' AND n.nspname IN ('core', 'truth', 'review', 'sim', 'corpus')
GROUP BY n.nspname, c.relname;
