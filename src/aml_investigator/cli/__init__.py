import argparse

from aml_investigator.cli import data, evaluation, monitoring, serve
from aml_investigator.core.logging import configure_logging


def main() -> None:
    parser = argparse.ArgumentParser(prog="aml")
    parser.add_argument("--database", help="database to work on (default: the one in the DSNs)")
    commands = parser.add_subparsers(required=True)
    for group in (data, monitoring, evaluation, serve):
        group.register(commands)

    args = parser.parse_args()
    configure_logging()
    args.handler(args)
