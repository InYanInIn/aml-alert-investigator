from itertools import batched
from typing import Any

from langchain_core.messages import ToolCall
from langchain_core.messages.tool import tool_call

from aml_investigator.investigation.agent.state import InvestigatedAlert

TRANSACTION_BATCH = 100
TRACE_HOPS = 2
PASSAGES_PER_QUERY = 3
ALWAYS_RELEVANT = "suspicion_reporting"
RULE_TOPICS = {
    "R1": "unusual_transactions",
    "R2": "unusual_transactions",
    "R3": "unusual_transactions",
    "R4": "unusual_transactions",
    "R5": "linked_transactions",
    "R6": "ongoing_monitoring",
}
REGULATION_QUERIES = {
    "suspicion_reporting": (
        '"reasonable grounds to suspect" FIU',
        "zawiadamia Generalnego Inspektora podejrzenie",
    ),
    "unusual_transactions": (
        'transaction "unusually large" complex purpose',
        "skomplikowanych nietypowy",
    ),
    "linked_transactions": (
        '"through linked transactions" "establishing a business relationship"',
        '"wydają się ze sobą powiązane" "transakcji okazjonalnej"',
    ),
    "ongoing_monitoring": (
        'transactions "consistent with" "risk profile" monitoring',
        '"bieżące monitorowanie stosunków gospodarczych"',
    ),
}


def alert_call() -> ToolCall:
    return tool_call(name="get_alert", args={}, id="alert")


def evidence_calls(alert: InvestigatedAlert) -> list[ToolCall]:
    account = {"account_id": alert.account_id}
    flagged = list(dict.fromkeys(txn_id for hit in alert.rule_hits for txn_id in hit.evidence_txn_ids))
    planned: list[tuple[str, dict[str, Any]]] = [
        ("get_account_profile", account),
        ("get_counterparties", account),
        ("list_transactions", account),
        *(("get_transactions", {"txn_ids": list(batch)}) for batch in batched(flagged, TRANSACTION_BATCH)),
        ("get_typology_candidates", {}),
        ("trace_funds", {**account, "direction": "forward", "max_hops": TRACE_HOPS}),
        ("trace_funds", {**account, "direction": "backward", "max_hops": TRACE_HOPS}),
    ]
    return [tool_call(name=name, args=args, id=f"evidence-{index}") for index, (name, args) in enumerate(planned)]


def regulation_calls(alert: InvestigatedAlert) -> list[ToolCall]:
    topics = dict.fromkeys([ALWAYS_RELEVANT, *(RULE_TOPICS[rule_prefix(hit.rule_id)] for hit in alert.rule_hits)])
    queries = [query for topic in topics for query in REGULATION_QUERIES[topic]]
    return [
        tool_call(
            name="search_regulations", args={"query": query, "top_k": PASSAGES_PER_QUERY}, id=f"regulations-{index}"
        )
        for index, query in enumerate(queries)
    ]


def rule_prefix(rule_id: str) -> str:
    return rule_id.split("_", 1)[0]
