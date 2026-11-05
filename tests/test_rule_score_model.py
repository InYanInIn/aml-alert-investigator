import hashlib
import json
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest
from langchain_core.messages import AIMessage, BaseMessage, ToolMessage

from aml_investigator.investigation.decision import Decision
from aml_investigator.investigation.models.rule_score import RuleScoreChatModel, rule_score_model
from aml_investigator.investigation.verify import Provenance, verify

AS_OF = "2022-09-03T00:00:00"
ALERT = {
    "alert_id": 1,
    "account_id": "A1",
    "alert_date": "2022-09-02",
    "as_of": AS_OF,
    "rule_hits": [
        {"rule_id": "R2_fan_out", "score": 6.0, "evidence_txn_ids": [11, 12]},
        {"rule_id": "R5_structuring", "score": 2.0, "evidence_txn_ids": [12]},
    ],
}
TRANSACTIONS = [
    {
        "txn_id": 11,
        "ts": "2022-09-02T10:15:00",
        "from_account": "A1",
        "to_account": "A2",
        "amount_paid": 1234567.891,
        "paid_currency": "US Dollar",
        "amount_received": 1100000.0,
        "received_currency": "Euro",
    },
    {
        "txn_id": 12,
        "ts": "2022-09-02T11:00:00",
        "from_account": "A3",
        "to_account": "A1",
        "amount_paid": 10.0,
        "paid_currency": "Euro",
        "amount_received": 9.5,
        "received_currency": "Swiss Franc",
    },
]
CANDIDATES = {
    "alert_id": 1,
    "candidates": [
        {
            "rule_id": "R2_fan_out",
            "score": 6.0,
            "evidence_txn_ids": [11, 12],
            "typologies": ["fan_out", "scatter_gather"],
        },
        {"rule_id": "R5_structuring", "score": 2.0, "evidence_txn_ids": [12], "typologies": ["structuring"]},
    ],
}
PASSAGE = {
    "passage_id": "AMLR art. 69(1)",
    "text": "x",
    "content_sha256": hashlib.sha256(b"x").hexdigest(),
    "rank": 0.1,
}
THRESHOLDS = {"R2_fan_out": 6.0, "R5_structuring": 4.0}


def tool_message(name: str, content: dict[str, Any]) -> ToolMessage:
    return ToolMessage(content="", tool_call_id=name, name=name, artifact={"structured_content": content})


RESULTS: list[tuple[str, dict[str, Any]]] = [
    ("get_alert", ALERT),
    ("get_transactions", {"as_of": AS_OF, "omitted": 0, "transactions": TRANSACTIONS}),
    ("get_typology_candidates", CANDIDATES),
    ("search_regulations", {"query": "q", "passages": [PASSAGE]}),
    ("search_regulations", {"query": "empty", "passages": []}),
]


def evidence() -> list[BaseMessage]:
    return [tool_message(name, content) for name, content in RESULTS]


def model(close_below: float) -> RuleScoreChatModel:
    return RuleScoreChatModel(thresholds=THRESHOLDS, close_below=close_below)


def provenance() -> Provenance:
    seen = Provenance()
    for name, content in RESULTS:
        seen = seen.including(name, content)
    return seen


@pytest.mark.parametrize(("close_below", "recommendation"), [(1.5, "escalate"), (1.51, "close")])
def test_recommends_close_only_below_the_operating_point(close_below: float, recommendation: str) -> None:
    drafted = model(close_below).with_structured_output(Decision).invoke(evidence())

    assert isinstance(drafted, Decision)
    assert drafted.recommendation == recommendation


def test_escalation_is_verifiable_and_cites_what_it_saw() -> None:
    drafted = model(1.0).with_structured_output(Decision).invoke(evidence())
    assert isinstance(drafted, Decision)

    assert verify(drafted, provenance(), datetime.fromisoformat(AS_OF)).errors == []
    assert [claim.text for claim in drafted.what] == [
        "The account paid 1,234,567.89 US Dollar on 2022-09-02.",
        "The account received 9.50 Swiss Franc on 2022-09-02.",
    ]
    assert drafted.why[0].passage_ids == ["AMLR art. 69(1)"]
    assert [(v.typology, v.verdict, v.txn_ids) for v in drafted.typologies] == [
        ("fan_out", "supported", [11, 12]),
        ("scatter_gather", "insufficient_evidence", []),
        ("structuring", "supported", [12]),
    ]


def test_close_supports_no_typology() -> None:
    drafted = model(10.0).with_structured_output(Decision).invoke(evidence())

    assert isinstance(drafted, Decision)
    assert {verdict.verdict for verdict in drafted.typologies} == {"insufficient_evidence"}


def test_makes_no_tool_calls_in_the_loop() -> None:
    reply = model(1.0).bind_tools([{"type": "function", "function": {"name": "trace_funds", "parameters": {}}}])

    answer = reply.invoke(evidence())

    assert isinstance(answer, AIMessage)
    assert answer.tool_calls == []


def test_reads_thresholds_and_operating_point_for_the_database(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / "config" / "thresholds").mkdir(parents=True)
    (tmp_path / "config" / "thresholds" / "aml_x.json").write_text(json.dumps({"thresholds": THRESHOLDS}))
    (tmp_path / "eval" / "results" / "aml_x" / "val").mkdir(parents=True)
    (tmp_path / "eval" / "results" / "aml_x" / "val" / "b1.json").write_text(
        json.dumps({"operating_point": {"threshold": 1.25}})
    )
    monkeypatch.chdir(tmp_path)

    loaded = rule_score_model("aml_x")

    assert (loaded.thresholds, loaded.close_below, loaded.get_name()) == (THRESHOLDS, 1.25, "rule-score")
