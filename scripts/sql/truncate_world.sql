DO $$
DECLARE
    world_tables text;
BEGIN
    SELECT string_agg(format('%I.%I', schemaname, tablename), ', ')
    INTO world_tables
    FROM pg_tables
    WHERE schemaname IN ('core', 'truth', 'review', 'sim', 'corpus');
    EXECUTE 'TRUNCATE ' || world_tables || ' RESTART IDENTITY CASCADE';
END
$$;
