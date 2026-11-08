import argparse
import asyncio
import json

import psycopg

from aml_investigator.cli.context import Commands, settings_for
from aml_investigator.cli.evaluation import positive_int
from aml_investigator.core.settings import load_settings
from aml_investigator.simulation import MAX_INVESTIGATIONS

SIM_SYSTEMS = ("b0", "b1", "b4", "scripted", "azure", "azure_profile")
CADENCES = (1, 3, 7)


def run_corpus_copy(args: argparse.Namespace) -> None:
    from aml_investigator.simulation.world import copy_corpus

    target = settings_for(args)
    source = load_settings().with_database(args.source)
    with psycopg.connect(source.owner_conninfo) as source_conn, psycopg.connect(target.owner_conninfo) as target_conn:
        print(f"passages in {target.database}: {copy_corpus(source_conn, target_conn)}")


def run_simulation(args: argparse.Namespace) -> None:
    from aml_investigator.evaluation.results import GitState
    from aml_investigator.simulation.loop import simulate
    from aml_investigator.simulation.results import refuse_second_opening, summary, write_result

    settings = settings_for(args)
    refuse_second_opening(settings.database, args.system)
    git_state = GitState.capture()
    run = asyncio.run(simulate(settings, seed=args.seed, system=args.system, cadence=args.cadence, limit=args.limit))
    path = write_result(run, git_state)
    print(summary(json.loads(path.read_text())))
    print(f"written to {path}")


def register(commands: Commands) -> None:
    simulation = commands.add_parser("sim").add_subparsers(required=True)

    corpus = simulation.add_parser("corpus", help="copy the embedded corpus from another database")
    corpus.add_argument("--from", dest="source", required=True)
    corpus.set_defaults(handler=run_corpus_copy)

    run = simulation.add_parser("run", help="run one seed of a world with one system; resets the world tables")
    run.add_argument("--seed", type=int, required=True)
    run.add_argument("--system", choices=SIM_SYSTEMS, required=True)
    run.add_argument("--cadence", type=int, choices=CADENCES, required=True)
    run.add_argument(
        "--limit", type=positive_int, default=MAX_INVESTIGATIONS, help="stop an agent after N investigations"
    )
    run.set_defaults(handler=run_simulation)
