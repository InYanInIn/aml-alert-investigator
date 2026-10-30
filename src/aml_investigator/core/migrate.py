from collections.abc import Iterable
from importlib import resources

import psycopg
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict

from aml_investigator.core.sql import load


def ensure_database(admin_dsn: str, name: str) -> bool:
    with psycopg.connect(admin_dsn, autocommit=True) as conn:
        if conn.execute(load(__package__, "database_exists"), (name,)).fetchone():
            return False
        conn.execute(sql.SQL(load(__package__, "create_database")).format(sql.Identifier(name)))
    return True


def migrate(owner_dsn: str, role_dsns: Iterable[str]) -> list[str]:
    applied: list[str] = []
    with psycopg.connect(owner_dsn, autocommit=True) as conn:
        conn.execute(load(__package__, "create_schema_migrations"))
        done = {row[0] for row in conn.execute(load(__package__, "applied_migrations"))}
        for script in sorted(resources.files("aml_investigator.core.migrations").iterdir(), key=lambda f: f.name):
            if not script.name.endswith(".sql") or script.name in done:
                continue
            with conn.transaction():
                conn.execute(script.read_text())
                conn.execute(load(__package__, "record_migration"), (script.name,))
            applied.append(script.name)
        for dsn in role_dsns:
            set_login_password(conn, dsn)
    return applied


def set_login_password(conn: psycopg.Connection[tuple[object, ...]], dsn: str) -> None:
    params = conninfo_to_dict(dsn)
    conn.execute(
        sql.SQL(load(__package__, "set_login_password")).format(
            sql.Identifier(str(params["user"])), sql.Literal(str(params["password"]))
        )
    )
