import argparse
from datetime import date
from pathlib import Path

import psycopg

from aml_investigator.settings import load_settings


def run_migrate(_: argparse.Namespace) -> None:
    from aml_investigator.migrate import migrate

    settings = load_settings()
    applied = migrate(settings.owner_dsn, settings.role_dsns)
    print(f"applied: {', '.join(applied) or 'nothing new'}")


def run_ingest(args: argparse.Namespace) -> None:
    from aml_investigator.ingest import ingest

    with psycopg.connect(load_settings().owner_dsn) as conn:
        report = ingest(conn, args.raw_dir, args.prefix, period_end=args.period_end, limit=args.limit)
    print(report)


def run_monitor(args: argparse.Namespace) -> None:
    from aml_investigator.monitor import run_fan_out

    with psycopg.connect(load_settings().owner_dsn) as conn:
        created = run_fan_out(conn, min_receivers=args.min_receivers, window_days=args.window_days)
    print(f"alerts created: {created}")


def run_mcp(args: argparse.Namespace) -> None:
    from aml_investigator.mcp_server import build_server

    build_server(load_settings().mcp_dsn).run(transport="http", host=args.host, port=args.port)


def run_api(args: argparse.Namespace) -> None:
    import uvicorn

    from aml_investigator.api import create_app

    uvicorn.run(create_app(load_settings(), mcp=args.mcp_url), host=args.host, port=args.port)


def main() -> None:
    parser = argparse.ArgumentParser(prog="aml")
    commands = parser.add_subparsers(required=True)

    commands.add_parser("migrate").set_defaults(handler=run_migrate)

    ingest = commands.add_parser("ingest")
    ingest.add_argument("--raw-dir", type=Path, default=Path("data/raw"))
    ingest.add_argument("--prefix", default="HI-Small")
    ingest.add_argument("--period-end", type=date.fromisoformat, default=date(2022, 9, 10))
    ingest.add_argument("--limit", type=int)
    ingest.set_defaults(handler=run_ingest)

    monitor = commands.add_parser("monitor")
    monitor.add_argument("--min-receivers", type=int, default=5)
    monitor.add_argument("--window-days", type=int, default=2)
    monitor.set_defaults(handler=run_monitor)

    mcp = commands.add_parser("mcp")
    mcp.add_argument("--host", default="127.0.0.1")
    mcp.add_argument("--port", type=int, default=8100)
    mcp.set_defaults(handler=run_mcp)

    api = commands.add_parser("api")
    api.add_argument("--host", default="127.0.0.1")
    api.add_argument("--port", type=int, default=8000)
    api.add_argument("--mcp-url", default="http://127.0.0.1:8100/mcp")
    api.set_defaults(handler=run_api)

    args = parser.parse_args()
    args.handler(args)
