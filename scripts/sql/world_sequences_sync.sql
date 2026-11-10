DO $$
DECLARE
    identity_column record;
BEGIN
    FOR identity_column IN
        SELECT n.nspname AS schema_name, c.relname AS table_name, a.attname AS column_name
        FROM pg_attribute a
        JOIN pg_class c ON c.oid = a.attrelid
        JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE a.attidentity <> '' AND n.nspname IN ('core', 'truth', 'review', 'sim', 'corpus')
    LOOP
        EXECUTE format(
            'SELECT setval(pg_get_serial_sequence(%L, %L), coalesce(max(%I), 1), max(%I) IS NOT NULL) FROM %I.%I',
            format('%I.%I', identity_column.schema_name, identity_column.table_name),
            identity_column.column_name,
            identity_column.column_name,
            identity_column.column_name,
            identity_column.schema_name,
            identity_column.table_name
        );
    END LOOP;
END
$$;
