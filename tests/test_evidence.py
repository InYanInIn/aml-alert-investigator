from pathlib import Path
from typing import Any

import pytest
from fastmcp import Client

from aml_investigator.corpus.loader import load_corpus
from aml_investigator.investigation.evidence import (
    PASSAGES_PER_QUERY,
    REGULATION_QUERIES,
    RULE_TOPICS,
    evidence_calls,
    regulation_calls,
)
from aml_investigator.mcp_server.server import build_server
from aml_investigator.monitoring.typologies import RULE_TYPOLOGIES
from tests.conftest import Database

SOURCES = Path("corpus/sources")
EXPECTED_TOP_PASSAGES = {
    "suspicion_reporting": ("AMLR art. 69(1)", "PL-AML art. 74 ust. 1"),
    "unusual_transactions": ("AMLR art. 34(2)", "PL-AML art. 43 ust. 4"),
    "linked_transactions": ("AMLR art. 19(1)", "PL-AML art. 35 ust. 1"),
    "ongoing_monitoring": ("AMLR art. 26(1)", "PL-AML art. 34 ust. 1"),
}


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def alert(*hits: tuple[str, list[int]]) -> dict[str, Any]:
    return {
        "account_id": "A1",
        "rule_hits": [{"rule_id": rule_id, "score": 1.0, "evidence_txn_ids": ids} for rule_id, ids in hits],
    }


def test_evidence_pack_fetches_every_flagged_transaction_in_batches() -> None:
    calls = evidence_calls(alert(("R1_fan_in", list(range(150))), ("R2_fan_out", list(range(100, 230)))))

    batches = [call["args"]["txn_ids"] for call in calls if call["name"] == "get_transactions"]
    assert [len(batch) for batch in batches] == [100, 100, 30]
    assert sorted(txn_id for batch in batches for txn_id in batch) == list(range(230))
    assert [call["name"] for call in calls if call["name"] != "get_transactions"] == [
        "get_account_profile",
        "get_counterparties",
        "list_transactions",
        "get_typology_candidates",
        "trace_funds",
        "trace_funds",
    ]
    assert len({call["id"] for call in calls}) == len(calls)
    assert all("alert_id" not in call["args"] for call in calls)


def test_regulation_queries_cover_each_topic_once_in_both_languages() -> None:
    calls = regulation_calls(alert(("R1_fan_in", []), ("R2_fan_out", []), ("R5_structuring", [])))

    queries = [call["args"]["query"] for call in calls]
    assert queries == [
        *REGULATION_QUERIES["suspicion_reporting"],
        *REGULATION_QUERIES["unusual_transactions"],
        *REGULATION_QUERIES["linked_transactions"],
    ]
    assert {call["args"]["top_k"] for call in calls} == {PASSAGES_PER_QUERY}


def test_every_rule_has_a_regulation_topic() -> None:
    assert set(RULE_TOPICS) == set(RULE_TYPOLOGIES)
    assert set(RULE_TOPICS.values()) | {"suspicion_reporting"} == set(REGULATION_QUERIES) == set(EXPECTED_TOP_PASSAGES)


@pytest.mark.anyio
async def test_every_shipped_query_ranks_its_passage_first_in_the_real_corpus(db: Database) -> None:
    with db.owner() as conn:
        load_corpus(conn, SOURCES)

    async with Client(build_server(db.mcp_dsn)) as client:
        for topic, queries in REGULATION_QUERIES.items():
            for query, expected in zip(queries, EXPECTED_TOP_PASSAGES[topic], strict=True):
                result = await client.call_tool("search_regulations", {"query": query, "top_k": PASSAGES_PER_QUERY})
                assert result.structured_content is not None
                found = [passage["passage_id"] for passage in result.structured_content["passages"]]
                assert found[:1] == [expected], (topic, query, found)
