import os
import sys
from graphlib import TopologicalSorter
from pathlib import Path

import psycopg
from psycopg.conninfo import make_conninfo

from aml_investigator.core.migrate import ensure_database, migrate
from aml_investigator.core.settings import Settings

SQL_DIR = Path(__file__).parent / "sql"
TRUNCATE = (SQL_DIR / "truncate_world.sql").read_text()
ROW_COUNTS = (SQL_DIR / "world_row_counts.sql").read_text()
COLUMNS = (SQL_DIR / "world_columns.sql").read_text()
FOREIGN_KEYS = (SQL_DIR / "world_foreign_keys.sql").read_text()
SEQUENCES = (SQL_DIR / "world_sequences_sync.sql").read_text()


def forwarded(dsn: str) -> str:
    return make_conninfo(dsn, host="127.0.0.1", port=os.environ["AKS_FORWARD_PORT"])


def target_settings(database: str) -> Settings:
    return Settings(
        _env_file=None,
        owner_dsn=forwarded(os.environ["AKS_OWNER_DSN"]),
        mcp_dsn=forwarded(os.environ["AKS_MCP_DSN"]),
        app_dsn=forwarded(os.environ["AKS_APP_DSN"]),
        evaluator_dsn=forwarded(os.environ["AKS_EVALUATOR_DSN"]),
    ).with_database(database)


def counts(dsn: str) -> dict[str, int]:
    with psycopg.connect(dsn) as conn:
        return {name: int(count) for name, count in conn.execute(ROW_COUNTS).fetchall()}


def load_order(source: psycopg.Connection[tuple[object, ...]]) -> list[tuple[str, str, list[str]]]:
    columns = {(schema, table): list(names) for schema, table, names in source.execute(COLUMNS).fetchall()}
    graph: dict[tuple[str, str], set[tuple[str, str]]] = {key: set() for key in columns}
    for schema, table, parent_schema, parent in source.execute(FOREIGN_KEYS).fetchall():
        if (schema, table) != (parent_schema, parent):
            graph[(schema, table)].add((parent_schema, parent))
    return [(*key, columns[key]) for key in TopologicalSorter(graph).static_order()]


def copy_data(source: Settings, target: Settings) -> None:
    with (
        psycopg.connect(source.owner_conninfo) as src,
        psycopg.connect(target.owner_conninfo) as dst,
        dst.transaction(),
    ):
        dst.execute(TRUNCATE)
        for schema, table, names in load_order(src):
            columns = ", ".join(f'"{name}"' for name in names)
            qualified = f'"{schema}"."{table}"'
            with (
                src.cursor().copy(f"COPY {qualified} ({columns}) TO STDOUT (FORMAT binary)") as out,
                dst.cursor().copy(f"COPY {qualified} ({columns}) FROM STDIN (FORMAT binary)") as into,
            ):
                for chunk in out:
                    into.write(chunk)
        dst.execute(SEQUENCES)


def main(database: str) -> None:
    source = Settings().with_database("aml_sim_ui")
    target = target_settings(database)
    ensure_database(target_settings("postgres").owner_conninfo, database)
    print("migrations applied:", migrate(target.owner_conninfo, target.role_dsns) or "none")
    copy_data(source, target)
    before, after = counts(source.owner_conninfo), counts(target.owner_conninfo)
    for name in before:
        print(f"{name}: {before[name]} -> {after.get(name)}")
    if before != after:
        raise SystemExit("row counts differ")
    print("row counts equal")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "aml_sim_ui")
