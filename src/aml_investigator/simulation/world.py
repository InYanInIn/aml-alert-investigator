import re
from typing import Any

import psycopg

from aml_investigator.core.errors import DomainError
from aml_investigator.core.sql import load

SIMULATION_DATABASE = re.compile(r"aml_sim_[a-z0-9_]+")

RESET_WORLD_SQL = load(__package__, "reset_world")
CLEAR_CORPUS_SQL = load(__package__, "clear_corpus")
COUNT_PASSAGES_SQL = load(__package__, "count_passages")
CORPUS_COPIES = (
    (load(__package__, "corpus_documents_out"), load(__package__, "corpus_documents_in")),
    (load(__package__, "corpus_passages_out"), load(__package__, "corpus_passages_in")),
)


class NotASimulationDatabaseError(DomainError):
    def __init__(self, name: str) -> None:
        super().__init__(f"{name!r} is not a simulation world; world operations need a database named aml_sim_*")


def require_simulation_database(name: str) -> None:
    if not SIMULATION_DATABASE.fullmatch(name):
        raise NotASimulationDatabaseError(name)


def reset_world(conn: psycopg.Connection[Any]) -> None:
    require_simulation_database(conn.info.dbname)
    conn.execute(RESET_WORLD_SQL)


def copy_corpus(source: psycopg.Connection[Any], target: psycopg.Connection[Any]) -> int:
    require_simulation_database(target.info.dbname)
    with target.transaction():
        target.execute(CLEAR_CORPUS_SQL)
        for copy_out, copy_in in CORPUS_COPIES:
            with source.cursor().copy(copy_out) as out, target.cursor().copy(copy_in) as into:
                for chunk in out:
                    into.write(chunk)
    [(passages,)] = target.execute(COUNT_PASSAGES_SQL).fetchall()
    return int(passages)
