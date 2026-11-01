from dataclasses import dataclass
from typing import Any

import psycopg

from aml_investigator.core.sql import load

CLEAR_CANDIDATE_TRUTH = load(__package__, "clear_candidate_truth")

FILL_CANDIDATE_TRUTH = load(__package__, "fill_candidate_truth")

SUMMARISE_CANDIDATE_TRUTH = load(__package__, "summarise_candidate_truth")


@dataclass(frozen=True)
class CandidateTruthReport:
    candidates: int
    with_laundering: int

    def __str__(self) -> str:
        return f"candidates: {self.candidates}, with laundering: {self.with_laundering}"


def compute_candidate_truth(conn: psycopg.Connection[Any]) -> CandidateTruthReport:
    with conn.transaction():
        conn.execute(CLEAR_CANDIDATE_TRUTH)
        conn.execute(FILL_CANDIDATE_TRUTH)
        [summary] = conn.execute(SUMMARISE_CANDIDATE_TRUTH).fetchall()
    return CandidateTruthReport(*summary)
