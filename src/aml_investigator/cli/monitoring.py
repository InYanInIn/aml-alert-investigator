import argparse
import json

import psycopg

from aml_investigator.cli.context import THRESHOLDS_DIR, Commands, settings_for


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


def register(commands: Commands) -> None:
    monitor = commands.add_parser("monitor").add_subparsers(required=True)
    monitor.add_parser("candidates").set_defaults(handler=run_candidates)
    monitor.add_parser("alerts").set_defaults(handler=run_alerts)

    truth = commands.add_parser("truth").add_subparsers(required=True)
    truth.add_parser("alerts").set_defaults(handler=run_alert_truth)
    truth.add_parser("candidates").set_defaults(handler=run_candidate_truth)

    calibrate = commands.add_parser("calibrate").add_subparsers(required=True)
    curves = calibrate.add_parser("curves")
    curves.add_argument("--suppression-days", type=int, default=3)
    curves.set_defaults(handler=run_curves)
    thresholds = calibrate.add_parser("thresholds")
    thresholds.add_argument("--budget", type=float, required=True)
    thresholds.add_argument("--suppression-days", type=int, default=3)
    thresholds.set_defaults(handler=run_thresholds)
