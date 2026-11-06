import argparse
import json
from pathlib import Path

import pytest
from fastmcp import Client

from aml_investigator.cli import data, evaluation
from aml_investigator.core.settings import MissingSettingError, Settings
from aml_investigator.corpus.embeddings import EMBEDDING_MODEL, embed_missing, passage_input
from aml_investigator.corpus.search import reciprocal_rank_fusion
from aml_investigator.evaluation.retrieval import (
    LabelledQuery,
    document_metrics,
    evaluate_retrieval,
    first_expected_ranks,
    first_rank,
    summarise,
)
from aml_investigator.mcp_server.schemas import PassageMatch
from aml_investigator.mcp_server.server import build_server
from tests.conftest import Database
from tests.test_evaluation import settings_of

DIMENSIONS = 1536
VOCABULARY = ("report", "examine", "inspektor")


def keyword_vector(text: str) -> list[float]:
    vector = [0.0] * DIMENSIONS
    for index, word in enumerate(VOCABULARY):
        if word in text.lower():
            vector[index] = 1.0
    vector[-1] = 0.01
    return vector


class KeywordEmbedder:
    def __init__(self) -> None:
        self.inputs: list[str] = []

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        self.inputs += texts
        return [keyword_vector(text) for text in texts]


def test_reciprocal_rank_fusion_rewards_agreement() -> None:
    assert reciprocal_rank_fusion([["a", "b", "c"], ["b", "d"]], k=60) == ["b", "a", "d", "c"]


def test_first_rank_is_one_based_and_none_when_absent() -> None:
    assert first_rank(["x", "y", "z"], ["z", "y"]) == 2
    assert first_rank(["x"], ["q"]) is None


def test_summarise_hit_at_k_and_mrr() -> None:
    scores = summarise([1, 3, None, 5])

    assert scores == {"hit@1": 0.25, "hit@3": 0.5, "hit@5": 0.75, "mrr": pytest.approx((1 + 1 / 3 + 0 + 1 / 5) / 4)}


def test_metrics_use_the_scope_of_each_document() -> None:
    queries = [
        LabelledQuery("q1", "one", ("AMLR art. 1", "PL-AML art. 2")),
        LabelledQuery("q2", "two", ("PL-AML art. 3",)),
    ]
    unfiltered = {"fts": [], "vector": ["AMLR art. 1", "PL-AML art. 3", "PL-AML art. 2"], "hybrid": []}
    within_pl = {"fts": [], "vector": ["PL-AML art. 3", "PL-AML art. 2"], "hybrid": []}
    within_amlr = {"fts": [], "vector": ["AMLR art. 1"], "hybrid": []}
    scoped = {"unfiltered": unfiltered, "AMLR": within_amlr, "PL-AML": within_pl}

    flat = document_metrics(queries, {"q1": scoped, "q2": scoped}, lambda _: "unfiltered")
    split = document_metrics(queries, {"q1": scoped, "q2": scoped}, lambda document: document)

    assert flat["PL-AML"]["queries"] == 2
    assert flat["PL-AML"]["vector"]["hit@1"] == 0
    assert split["PL-AML"]["vector"]["hit@1"] == 0.5
    assert split["PL-AML"]["vector"]["hit@3"] == 1
    assert split["AMLR"]["vector"]["hit@1"] == 1
    assert split["PL-AML"]["fts"]["mrr"] == 0
    assert first_expected_ranks(queries, {"q1": scoped, "q2": scoped})[1]["first_expected_rank"]["PL-AML"] == {
        "PL-AML": {"fts": None, "vector": 1, "hybrid": None}
    }


def test_passage_input_prepends_the_heading() -> None:
    assert passage_input("Record retention", "text") == "Record retention\n\ntext"
    assert passage_input(None, "text") == "text"


def test_embed_missing_embeds_only_passages_without_embedding(db: Database, regulations: list[str]) -> None:
    embedder = KeywordEmbedder()
    with db.owner() as conn:
        conn.execute("UPDATE corpus.passages SET heading = 'Reporting' WHERE passage_id = %s", (regulations[0],))
        assert embed_missing(conn, embedder).passages == 3
        assert embed_missing(conn, embedder).passages == 0
        conn.execute("UPDATE corpus.passages SET embedding = NULL WHERE passage_id = %s", (regulations[1],))
        embedder.inputs.clear()
        report = embed_missing(conn, embedder)
        stored = conn.execute("SELECT count(embedding) FROM corpus.passages").fetchone()

    assert report.passages == 1
    assert len(embedder.inputs) == 1
    assert stored == (3,)


def test_embedding_input_contains_heading_and_text(db: Database, regulations: list[str]) -> None:
    embedder = KeywordEmbedder()
    with db.owner() as conn:
        conn.execute("UPDATE corpus.passages SET heading = 'Reporting' WHERE passage_id = %s", (regulations[0],))
        embed_missing(conn, embedder)

    assert any(text.startswith("Reporting\n\nObliged entities shall report") for text in embedder.inputs)


@pytest.fixture
def embedded_corpus(db: Database, regulations: list[str]) -> list[str]:
    with db.owner() as conn:
        for old, new in (("amlr", "AMLR"), ("pl-aml", "PL-AML")):
            conn.execute(
                "INSERT INTO corpus.documents SELECT %s, title, language, version, source_url, content_sha256 "
                "FROM corpus.documents WHERE doc_id = %s",
                (new, old),
            )
            conn.execute("UPDATE corpus.passages SET doc_id = %s WHERE doc_id = %s", (new, old))
        embed_missing(conn, KeywordEmbedder())
    return regulations


@pytest.mark.usefixtures("embedded_corpus")
def test_retrieval_evaluation_writes_provenance_and_scores(db: Database, tmp_path: Path) -> None:
    queries = tmp_path / "eval" / "retrieval" / "queries.json"
    queries.parent.mkdir(parents=True)
    queries.write_text(
        json.dumps(
            {
                "queries": [
                    {"id": "q1", "query": "report", "expected": ["AMLR art. 69(1)"]},
                    {"id": "q2", "query": "inspektor", "expected": ["PL-AML art. 74 ust. 1", "PL-AML art. 99"]},
                ]
            }
        )
    )
    settings = settings_of(db)

    path, table = evaluate_retrieval(
        settings, workspace=tmp_path, embed=lambda texts: [keyword_vector(t) for t in texts]
    )

    result = json.loads(path.read_text())
    assert path == tmp_path / "eval" / "results" / settings.database / "retrieval.json"
    assert result["embedding_model"] == EMBEDDING_MODEL
    assert result["missing_expected_passages"] == ["PL-AML art. 99"]
    assert result["passages"] == {"embedded": 3, "total": 3}
    assert result["metrics_unfiltered"]["AMLR"]["vector"]["hit@1"] == 1
    assert result["metrics_unfiltered"]["PL-AML"]["vector"]["hit@1"] == 1
    assert result["metrics_per_document"]["PL-AML"]["vector"]["hit@1"] == 1
    assert result["queries"][0]["first_expected_rank"]["AMLR"]["AMLR"]["vector"] == 1
    assert {"git_commit", "git_dirty", "queries_sha256", "created_at"} <= result.keys()
    assert "hit@5" in table


def test_azure_endpoint_is_required_for_embedding() -> None:
    settings = Settings(_env_file=None, azure_openai_endpoint=None)

    with pytest.raises(MissingSettingError, match="AML_AZURE_OPENAI_ENDPOINT"):
        _ = settings.azure_endpoint


def test_cli_wires_corpus_embed_and_retrieval_evaluation() -> None:
    parser = argparse.ArgumentParser()
    commands = parser.add_subparsers(required=True)
    data.register(commands)
    evaluation.register(commands)

    assert parser.parse_args(["corpus", "embed"]).handler is data.run_corpus_embed
    assert parser.parse_args(["eval", "retrieval"]).handler is evaluation.run_retrieval


async def embed_query(query: str) -> list[float]:
    return keyword_vector(query)


@pytest.mark.anyio
@pytest.mark.usefixtures("embedded_corpus")
@pytest.mark.parametrize(("top_k", "expected"), [(1, ["AMLR", "PL-AML"]), (2, ["AMLR", "AMLR", "PL-AML"])])
async def test_search_regulations_returns_top_k_from_each_law(db: Database, top_k: int, expected: list[str]) -> None:
    async with Client(build_server(db.mcp_dsn, embed_query=embed_query)) as client:
        result = await client.call_tool("search_regulations", {"query": "report", "top_k": top_k})

    passages = (result.structured_content or {})["passages"]
    assert [passage["doc_id"] for passage in passages] == expected
    assert passages[0]["passage_id"] == "AMLR art. 69(1)"
    assert all(set(passage) == set(PassageMatch.model_fields) for passage in passages)


@pytest.mark.anyio
@pytest.mark.usefixtures("embedded_corpus")
async def test_search_regulations_without_an_embedder_is_full_text(db: Database) -> None:
    async with Client(build_server(db.mcp_dsn)) as client:
        result = await client.call_tool("search_regulations", {"query": "report"})

    passages = (result.structured_content or {})["passages"]
    assert [passage["passage_id"] for passage in passages] == ["AMLR art. 69(1)"]
