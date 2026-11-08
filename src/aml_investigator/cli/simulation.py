import argparse

import psycopg

from aml_investigator.cli.context import Commands, settings_for
from aml_investigator.core.settings import load_settings


def run_corpus_copy(args: argparse.Namespace) -> None:
    from aml_investigator.simulation.world import copy_corpus

    target = settings_for(args)
    source = load_settings().with_database(args.source)
    with psycopg.connect(source.owner_conninfo) as source_conn, psycopg.connect(target.owner_conninfo) as target_conn:
        print(f"passages in {target.database}: {copy_corpus(source_conn, target_conn)}")


def register(commands: Commands) -> None:
    simulation = commands.add_parser("sim").add_subparsers(required=True)

    corpus = simulation.add_parser("corpus", help="copy the embedded corpus from another database")
    corpus.add_argument("--from", dest="source", required=True)
    corpus.set_defaults(handler=run_corpus_copy)
