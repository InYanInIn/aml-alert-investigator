from collections.abc import Iterable
from importlib import resources

import psycopg
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict


def ensure_database(admin_dsn: str, name: str) -> bool:
    with psycopg.connect(admin_dsn, autocommit=True) as conn:
        if conn.execute("SELECT 1 FROM pg_database WHERE datname = %s", (name,)).fetchone():
            return False
        conn.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
    return True


def migrate(owner_dsn: str, role_dsns: Iterable[str]) -> list[str]:
    applied: list[str] = []
    with psycopg.connect(owner_dsn, autocommit=True) as conn:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS public.schema_migrations "
            "(version text PRIMARY KEY, applied_at timestamptz NOT NULL DEFAULT now())"
        )
        done = {row[0] for row in conn.execute("SELECT version FROM public.schema_migrations")}
        for script in sorted(resources.files("aml_investigator.migrations").iterdir(), key=lambda f: f.name):
            if not script.name.endswith(".sql") or script.name in done:
                continue
            with conn.transaction():
                conn.execute(script.read_text())
                conn.execute("INSERT INTO public.schema_migrations (version) VALUES (%s)", (script.name,))
            applied.append(script.name)
        for dsn in role_dsns:
            set_login_password(conn, dsn)
    return applied


def set_login_password(conn: psycopg.Connection[tuple[object, ...]], dsn: str) -> None:
    params = conninfo_to_dict(dsn)
    conn.execute(
        sql.SQL("ALTER ROLE {} WITH LOGIN PASSWORD {}").format(
            sql.Identifier(str(params["user"])), sql.Literal(str(params["password"]))
        )
    )
