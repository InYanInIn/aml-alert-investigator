import argparse
from datetime import date
from pathlib import Path

import psycopg

from aml_investigator.cli.context import Commands, settings_for
from aml_investigator.core.settings import load_settings

CORPUS_SOURCES = Path("corpus/sources")


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


def register(commands: Commands) -> None:
    commands.add_parser("migrate").set_defaults(handler=run_migrate)

    ingest = commands.add_parser("ingest")
    ingest.add_argument("--raw-dir", type=Path, default=Path("data/raw"))
    ingest.add_argument("--prefix", default="HI-Small")
    ingest.add_argument("--period-end", type=date.fromisoformat, default=date(2022, 9, 10))
    ingest.add_argument("--limit", type=int)
    ingest.set_defaults(handler=run_ingest)

    commands.add_parser("references").set_defaults(handler=run_references)

    corpus = commands.add_parser("corpus").add_subparsers(required=True)
    corpus.add_parser("fetch").set_defaults(handler=run_corpus_fetch)
    corpus.add_parser("load").set_defaults(handler=run_corpus_load)
