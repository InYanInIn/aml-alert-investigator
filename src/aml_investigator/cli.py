import argparse
import asyncio
import json
from collections import Counter
from datetime import date
from pathlib import Path

import psycopg

from aml_investigator.core.logging import configure_logging
from aml_investigator.core.settings import Settings, load_settings

THRESHOLDS_DIR = Path("config/thresholds")
CORPUS_SOURCES = Path("corpus/sources")


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


def run_alert_truth(args: argparse.Namespace) -> None:
    from aml_investigator.truth.alert_truth import compute_alert_truth

    with psycopg.connect(settings_for(args).owner_conninfo) as conn:
        print(compute_alert_truth(conn))


def run_candidate_truth(args: argparse.Namespace) -> None:
    from aml_investigator.truth.candidate_truth import compute_candidate_truth

    with psycopg.connect(settings_for(args).owner_conninfo) as conn:
        print(compute_candidate_truth(conn))


def test_reason(args: argparse.Namespace) -> str | None:
    if args.split != "test":
        return None
    if not args.open_test or not args.reason:
        raise SystemExit("the test split needs --open-test and --reason; every opening is logged")
    return str(args.reason)


def run_sample(args: argparse.Namespace) -> None:
    from aml_investigator.evaluation.samples import create_sample

    path = create_sample(
        settings_for(args), split=args.split, size=args.size, seed=args.seed, test_reason=test_reason(args)
    )
    print(f"sample written to {path}")


def run_evaluation(args: argparse.Namespace) -> None:
    from aml_investigator.evaluation.runner import evaluate

    result = asyncio.run(
        evaluate(settings_for(args), system=args.system, split=args.split, test_reason=test_reason(args))
    )
    print(result.summary())


def run_references(args: argparse.Namespace) -> None:
    from aml_investigator.data.references import fill_references

    with psycopg.connect(settings_for(args).owner_conninfo) as conn:
        print(f"references filled: {fill_references(conn)}")


def run_corpus_fetch(_: argparse.Namespace) -> None:
    from aml_investigator.corpus.sources import fetch_sources

    for path in fetch_sources(CORPUS_SOURCES):
        print(f"verified {path}")


def run_corpus_load(args: argparse.Namespace) -> None:
    from aml_investigator.corpus.loader import load_corpus

    with psycopg.connect(settings_for(args).owner_conninfo) as conn:
        print(load_corpus(conn, CORPUS_SOURCES))


def run_injections(args: argparse.Namespace) -> None:
    from aml_investigator.evaluation.injections import create_injection_set

    print(f"injection set written to {create_injection_set(settings_for(args), size=args.size, seed=args.seed)}")


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

    build_server(settings_for(args).mcp_conninfo, reference_variant=args.reference_variant).run(
        transport="http", host=args.host, port=args.port
    )


def run_api(args: argparse.Namespace) -> None:
    import uvicorn

    from aml_investigator.investigation.models.rule_score import rule_score_model
    from aml_investigator.web.app import create_app

    settings = settings_for(args)
    app = create_app(settings, mcp=args.mcp_url, model=rule_score_model(settings.database))
    uvicorn.run(app, host=args.host, port=args.port, log_config=None)


def run_investigate(args: argparse.Namespace) -> None:
    routes, statuses = asyncio.run(investigate_day(settings_for(args), args.day, args.limit))
    print(f"investigated: {routes.total()}")
    for route, count in sorted(routes.items()):
        print(f"{route}: {count}")
    for status, count in sorted(statuses.items()):
        print(f"verification {status}: {count}")


async def investigate_day(settings: Settings, day: date, limit: int | None) -> tuple[Counter[str], Counter[str]]:
    from langchain.mcp import MCPAdapter

    from aml_investigator.core.tracing import tracing_callbacks
    from aml_investigator.investigation.models.rule_score import rule_score_model
    from aml_investigator.investigation.service import open_investigation_service
    from aml_investigator.mcp_server.server import build_server

    routes: Counter[str] = Counter()
    statuses: Counter[str] = Counter()
    async with (
        MCPAdapter(build_server(settings.mcp_conninfo)) as mcp,
        open_investigation_service(
            app_conninfo=settings.app_conninfo,
            mcp=mcp,
            model=rule_score_model(settings.database),
            callbacks=tracing_callbacks(settings),
        ) as service,
    ):
        for alert_id in await service.uninvestigated_alerts(day, limit):
            outcome = await service.investigate(alert_id)
            routes[outcome.route] += 1
            statuses[outcome.verification_status] += 1
    return routes, statuses


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

    truth = commands.add_parser("truth").add_subparsers(required=True)
    truth.add_parser("alerts").set_defaults(handler=run_alert_truth)
    truth.add_parser("candidates").set_defaults(handler=run_candidate_truth)

    commands.add_parser("references").set_defaults(handler=run_references)

    corpus = commands.add_parser("corpus").add_subparsers(required=True)
    corpus.add_parser("fetch").set_defaults(handler=run_corpus_fetch)
    corpus.add_parser("load").set_defaults(handler=run_corpus_load)

    evaluation = commands.add_parser("eval").add_subparsers(required=True)
    injections = evaluation.add_parser("injections")
    injections.add_argument("--size", type=int, default=40)
    injections.add_argument("--seed", type=int, default=20261102)
    injections.set_defaults(handler=run_injections)
    sample = evaluation.add_parser("sample")
    sample.add_argument("--split", choices=["val", "test"], required=True)
    sample.add_argument("--size", type=int)
    sample.add_argument("--seed", type=int, default=20261030)
    sample.add_argument("--open-test", action="store_true")
    sample.add_argument("--reason")
    sample.set_defaults(handler=run_sample)
    run = evaluation.add_parser("run")
    run.add_argument("--system", required=True)
    run.add_argument("--split", choices=["val", "test"], required=True)
    run.add_argument("--open-test", action="store_true")
    run.add_argument("--reason")
    run.set_defaults(handler=run_evaluation)

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
    mcp.add_argument("--reference-variant", choices=["clean", "injected"], default="clean")
    mcp.set_defaults(handler=run_mcp)

    investigate = commands.add_parser("investigate")
    investigate.add_argument("--day", type=date.fromisoformat, required=True)
    investigate.add_argument("--limit", type=int)
    investigate.set_defaults(handler=run_investigate)

    api = commands.add_parser("api")
    api.add_argument("--host", default="127.0.0.1")
    api.add_argument("--port", type=int, default=8000)
    api.add_argument("--mcp-url", default="http://127.0.0.1:8100/mcp")
    api.set_defaults(handler=run_api)

    args = parser.parse_args()
    configure_logging()
    args.handler(args)
