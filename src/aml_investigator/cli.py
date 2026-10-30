import argparse
import json
from datetime import date
from pathlib import Path

import psycopg

from aml_investigator.core.logging import configure_logging
from aml_investigator.core.settings import Settings, load_settings

THRESHOLDS_DIR = Path("config/thresholds")


def settings_for(args: argparse.Namespace) -> Settings:
    settings = load_settings()
    return settings.with_database(args.database) if args.database else settings


def run_migrate(args: argparse.Namespace) -> None:
    from aml_investigator.core.migrate import ensure_database, migrate

    settings = settings_for(args)
    if ensure_database(load_settings().owner_conninfo, settings.database):
        print(f"created database {settings.database}")
    applied = migrate(settings.owner_conninfo, settings.role_dsns)
    print(f"applied: {', '.join(applied) or 'nothing new'}")


def run_ingest(args: argparse.Namespace) -> None:
    from aml_investigator.data.ingest import ingest

    with psycopg.connect(settings_for(args).owner_conninfo) as conn:
        report = ingest(conn, args.raw_dir, args.prefix, period_end=args.period_end, limit=args.limit)
    print(report)


def run_candidates(args: argparse.Namespace) -> None:
    from aml_investigator.monitoring.rules import generate_candidates

    with psycopg.connect(settings_for(args).owner_conninfo) as conn:
        for rule_id, count in generate_candidates(conn).items():
            print(f"{rule_id}: {count} candidates")


def run_alerts(args: argparse.Namespace) -> None:
    from aml_investigator.monitoring.alerts import build_alerts

    settings = settings_for(args)
    config = json.loads((THRESHOLDS_DIR / f"{settings.database}.json").read_text())
    with psycopg.connect(settings.owner_conninfo) as conn:
        created = build_alerts(conn, config["thresholds"], suppression_days=config["suppression_days"])
    print(f"alerts created: {created}")


def run_truth(args: argparse.Namespace) -> None:
    from aml_investigator.truth.alert_truth import compute_alert_truth

    with psycopg.connect(settings_for(args).owner_conninfo) as conn:
        print(compute_alert_truth(conn))


def run_curves(args: argparse.Namespace) -> None:
    from aml_investigator.monitoring.calibration import rule_curves, train_split

    with psycopg.connect(settings_for(args).owner_conninfo) as conn:
        train = train_split(conn)["train"]
        print(f"train {train.first} .. {train.last}, suppression {args.suppression_days} days")
        print("rule                  quantile  threshold  alerts/day  positive  attempts")
        for point in rule_curves(conn, train, suppression_days=args.suppression_days):
            print(
                f"{point.rule_id:<21} {point.quantile:>8} {point.threshold:>10.2f} {point.alerts_per_day:>11.1f} "
                f"{point.positive_share:>9.1%} {point.attempts_touched:>5}/{point.attempts_in_window}"
            )


def run_thresholds(args: argparse.Namespace) -> None:
    from aml_investigator.monitoring.calibration import choose_thresholds, feasibility, train_split, write_thresholds

    settings = settings_for(args)
    with psycopg.connect(settings.owner_conninfo) as conn:
        splits = train_split(conn)
        thresholds = choose_thresholds(
            conn, splits["train"], budget_per_day=args.budget, suppression_days=args.suppression_days
        )
        write_thresholds(
            THRESHOLDS_DIR / f"{settings.database}.json",
            thresholds,
            budget_per_day=args.budget,
            suppression_days=args.suppression_days,
            train=splits["train"],
        )
        check = feasibility(conn, splits["val"], thresholds, suppression_days=args.suppression_days)
    for rule_id, threshold in thresholds.items():
        print(f"{rule_id}: {threshold}")
    print(
        f"val: {check.alerts_per_day:.1f} alerts/day, {check.positive_per_day:.1f} positive/day, "
        f"projected test positives {check.projected_test_positives}"
    )


def run_mcp(args: argparse.Namespace) -> None:
    from aml_investigator.mcp_server.server import build_server

    build_server(settings_for(args).mcp_conninfo).run(transport="http", host=args.host, port=args.port)


def run_api(args: argparse.Namespace) -> None:
    import uvicorn

    from aml_investigator.web.app import create_app

    uvicorn.run(create_app(settings_for(args), mcp=args.mcp_url), host=args.host, port=args.port, log_config=None)


def main() -> None:
    parser = argparse.ArgumentParser(prog="aml")
    parser.add_argument("--database", help="database to work on (default: the one in the DSNs)")
    commands = parser.add_subparsers(required=True)

    commands.add_parser("migrate").set_defaults(handler=run_migrate)

    ingest = commands.add_parser("ingest")
    ingest.add_argument("--raw-dir", type=Path, default=Path("data/raw"))
    ingest.add_argument("--prefix", default="HI-Small")
    ingest.add_argument("--period-end", type=date.fromisoformat, default=date(2022, 9, 10))
    ingest.add_argument("--limit", type=int)
    ingest.set_defaults(handler=run_ingest)

    monitor = commands.add_parser("monitor").add_subparsers(required=True)
    monitor.add_parser("candidates").set_defaults(handler=run_candidates)
    monitor.add_parser("alerts").set_defaults(handler=run_alerts)

    commands.add_parser("truth").set_defaults(handler=run_truth)

    calibrate = commands.add_parser("calibrate").add_subparsers(required=True)
    curves = calibrate.add_parser("curves")
    curves.add_argument("--suppression-days", type=int, default=3)
    curves.set_defaults(handler=run_curves)
    thresholds = calibrate.add_parser("thresholds")
    thresholds.add_argument("--budget", type=float, required=True)
    thresholds.add_argument("--suppression-days", type=int, default=3)
    thresholds.set_defaults(handler=run_thresholds)

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
    configure_logging()
    args.handler(args)
