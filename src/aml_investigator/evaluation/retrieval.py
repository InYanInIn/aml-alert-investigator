import json
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import psycopg

from aml_investigator.core.settings import Settings
from aml_investigator.core.sql import load
from aml_investigator.corpus.embeddings import EMBEDDING_MODEL, azure_embeddings, vector_literal
from aml_investigator.corpus.search import RRF_K, reciprocal_rank_fusion
from aml_investigator.evaluation.results import RESULTS_DIR, GitState, sha256
from aml_investigator.mcp_server.store import MAX_PASSAGES, SEARCH_REGULATIONS_SQL

QUERIES_PATH = Path("eval/retrieval/queries.json")
VECTOR_SQL = load("aml_investigator.mcp_server", "search_regulations_vector")
DOCUMENTS = ("AMLR", "PL-AML")
MODES = ("fts", "vector", "hybrid")
HIT_CUTOFFS = (1, 3, 5)
DEPTH = 50

type Ranking = list[str]
type Rankings = dict[str, Ranking]


@dataclass(frozen=True)
class LabelledQuery:
    query_id: str
    text: str
    expected: tuple[str, ...]


def load_queries(path: Path) -> list[LabelledQuery]:
    return [
        LabelledQuery(entry["id"], entry["query"], tuple(entry["expected"]))
        for entry in json.loads(path.read_text())["queries"]
    ]


def first_rank(ranking: Sequence[str], relevant: Sequence[str]) -> int | None:
    return next((position for position, passage_id in enumerate(ranking, start=1) if passage_id in relevant), None)


def relevant_in(document: str, query: LabelledQuery) -> list[str]:
    return [passage_id for passage_id in query.expected if passage_id.startswith(f"{document} ")]


def summarise(ranks: Sequence[int | None]) -> dict[str, float]:
    count = len(ranks)
    scores = {f"hit@{k}": sum(1 for rank in ranks if rank is not None and rank <= k) / count for k in HIT_CUTOFFS}
    scores["mrr"] = sum(0 if rank is None else 1 / rank for rank in ranks) / count
    return scores


def search_rankings(conn: psycopg.Connection[tuple[object, ...]], text: str, embedding: list[float]) -> Rankings:
    fts = [str(row[0]) for row in conn.execute(SEARCH_REGULATIONS_SQL, {"query": text, "top_k": DEPTH}).fetchall()]
    vector_params = {"embedding": vector_literal(embedding), "top_k": DEPTH}
    vector = [str(row[0]) for row in conn.execute(VECTOR_SQL, vector_params).fetchall()]
    return {"fts": fts, "vector": vector, "hybrid": reciprocal_rank_fusion([fts, vector])}


def evaluate_rankings(
    queries: Sequence[LabelledQuery], rankings: Mapping[str, Rankings]
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    metrics: dict[str, Any] = {}
    for document in DOCUMENTS:
        asked = [query for query in queries if relevant_in(document, query)]
        metrics[document] = {"queries": len(asked)} | {
            mode: summarise([first_rank(rankings[q.query_id][mode], relevant_in(document, q)) for q in asked])
            for mode in MODES
        }
    per_query = [
        {
            "id": query.query_id,
            "first_expected_rank": {
                document: {
                    mode: first_rank(rankings[query.query_id][mode], relevant_in(document, query)) for mode in MODES
                }
                for document in DOCUMENTS
                if relevant_in(document, query)
            },
        }
        for query in queries
    ]
    return metrics, per_query


def missing_passages(conn: psycopg.Connection[tuple[object, ...]], queries: Sequence[LabelledQuery]) -> list[str]:
    wanted = sorted({passage_id for query in queries for passage_id in query.expected})
    rows = conn.execute("SELECT passage_id FROM corpus.passages WHERE passage_id = ANY(%s)", (wanted,)).fetchall()
    return sorted(set(wanted) - {str(row[0]) for row in rows})


def table(metrics: Mapping[str, Any]) -> str:
    header = f"{'document':<8} {'mode':<7} " + " ".join(f"{f'hit@{k}':>6}" for k in HIT_CUTOFFS) + f" {'mrr':>6}"
    lines = [header]
    for document in DOCUMENTS:
        for mode in MODES:
            scores = metrics[document][mode]
            cells = " ".join(f"{scores[f'hit@{k}']:>6.2f}" for k in HIT_CUTOFFS)
            lines.append(f"{document:<8} {mode:<7} {cells} {scores['mrr']:>6.2f}")
    return "\n".join(lines)


def evaluate_retrieval(
    settings: Settings,
    *,
    workspace: Path = Path(),
    embed: Callable[[list[str]], list[list[float]]] | None = None,
) -> tuple[Path, str]:
    queries = load_queries(workspace / QUERIES_PATH)
    embed_texts = embed or azure_embeddings(settings.azure_endpoint).embed_documents
    embeddings = embed_texts([query.text for query in queries])
    with psycopg.connect(settings.evaluator_conninfo) as conn:
        missing = missing_passages(conn, queries)
        rankings = {
            query.query_id: search_rankings(conn, query.text, embedding)
            for query, embedding in zip(queries, embeddings, strict=True)
        }
        embedded, total = conn.execute("SELECT count(embedding), count(*) FROM corpus.passages").fetchall()[0]
    metrics, per_query = evaluate_rankings(queries, rankings)
    git_state = GitState.capture()
    document = {
        "database": settings.database,
        "created_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "git_commit": git_state.commit,
        "git_dirty": git_state.dirty,
        "queries_sha256": sha256(workspace / QUERIES_PATH),
        "embedding_model": EMBEDDING_MODEL,
        "passages": {"embedded": embedded, "total": total},
        "depth": DEPTH,
        "rrf_k": RRF_K,
        "default_top_k": MAX_PASSAGES,
        "missing_expected_passages": missing,
        "metrics": metrics,
        "queries": per_query,
    }
    path = workspace / RESULTS_DIR / settings.database / "retrieval.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(document, indent=2) + "\n")
    return path, table(metrics)
