import argparse
import asyncio
import json
from pathlib import Path

import psycopg

from aml_investigator.cli.context import Commands, settings_for
from aml_investigator.cli.evaluation import positive_int
from aml_investigator.core.settings import load_settings
from aml_investigator.simulation import MAX_INVESTIGATIONS

SIM_SYSTEMS = ("b0", "b1", "b4", "scripted", "azure", "azure_profile")
CADENCES = (1, 3, 7)


def non_negative_int(text: str) -> int:
    value = int(text)
    if value < 0:
        raise argparse.ArgumentTypeError(f"must be zero or more, got {value}")
    return value


def run_corpus_copy(args: argparse.Namespace) -> None:
    from aml_investigator.simulation.world import copy_corpus

    target = settings_for(args)
    source = load_settings().with_database(args.source)
    with psycopg.connect(source.owner_conninfo) as source_conn, psycopg.connect(target.owner_conninfo) as target_conn:
        print(f"passages in {target.database}: {copy_corpus(source_conn, target_conn)}")


def run_simulation(args: argparse.Namespace) -> None:
    from aml_investigator.evaluation.results import GitState
    from aml_investigator.simulation.loop import simulate
    from aml_investigator.simulation.results import refuse_second_opening, summary, thresholds_digest, write_result

    settings = settings_for(args)
    refuse_second_opening(settings.database, args.system)
    thresholds_digest(settings.database)
    git_state = GitState.capture()
    run = asyncio.run(simulate(settings, seed=args.seed, system=args.system, cadence=args.cadence, limit=args.limit))
    path = write_result(run, git_state)
    print(summary(json.loads(path.read_text())))
    print(f"written to {path}")


def run_calibration(args: argparse.Namespace) -> None:
    from aml_investigator.simulation.loop import calibrate_world

    thresholds, point = asyncio.run(calibrate_world(settings_for(args), seed=args.seed, budget_per_day=args.budget))
    print(f"thresholds written to {thresholds}\noperating point written to {point}")


def run_report(_: argparse.Namespace) -> None:
    from aml_investigator.simulation.results import update_simulation_report

    print(f"report written to {update_simulation_report(Path())}")


def register(commands: Commands) -> None:
    simulation = commands.add_parser("sim").add_subparsers(required=True)

    corpus = simulation.add_parser("corpus", help="copy the embedded corpus from another database")
    corpus.add_argument("--from", dest="source", required=True)
    corpus.set_defaults(handler=run_corpus_copy)

    run = simulation.add_parser("run", help="run one seed of a world with one system; resets the world tables")
    run.add_argument("--seed", type=non_negative_int, required=True)
    run.add_argument("--system", choices=SIM_SYSTEMS, required=True)
    run.add_argument("--cadence", type=int, choices=CADENCES, required=True)
    run.add_argument(
        "--limit", type=positive_int, default=MAX_INVESTIGATIONS, help="stop an agent after N investigations"
    )
    run.set_defaults(handler=run_simulation)

    calibrate = simulation.add_parser("calibrate", help="thresholds and the B1 operating point from a world's tape")
    calibrate.add_argument("--seed", type=int, required=True)
    calibrate.add_argument("--budget", type=float, required=True)
    calibrate.set_defaults(handler=run_calibration)

    report = simulation.add_parser("report", help="regenerate the M9 section of docs/results.md from result files")
    report.set_defaults(handler=run_report)
