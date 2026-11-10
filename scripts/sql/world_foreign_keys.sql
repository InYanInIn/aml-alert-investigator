SELECT cn.nspname, c.relname, pn.nspname, p.relname
FROM pg_constraint k
JOIN pg_class c ON c.oid = k.conrelid
JOIN pg_namespace cn ON cn.oid = c.relnamespace
JOIN pg_class p ON p.oid = k.confrelid
JOIN pg_namespace pn ON pn.oid = p.relnamespace
WHERE k.contype = 'f' AND cn.nspname IN ('core', 'truth', 'review', 'sim', 'corpus');
