import hashlib
import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Literal

from pydantic import BaseModel

from aml_investigator.investigation.decision import Decision, Fact

AMOUNT_TOLERANCE = Decimal("0.01")
NUMBER = re.compile(r"(?<!\w)\d+(?:,\d{3})*(?:\.\d+)?(?!\w)")
TRANSACTION_TOOLS = frozenset({"list_transactions", "get_transactions"})


@dataclass(frozen=True)
class Provenance:
    transactions: dict[int, dict[str, Any]] = field(default_factory=dict)
    passages: dict[str, dict[str, Any]] = field(default_factory=dict)

    def including(self, tool: str, result: dict[str, Any]) -> "Provenance":
        transactions = dict(self.transactions)
        for record in transaction_records(tool, result):
            transactions[record["txn_id"]] = {**transactions.get(record["txn_id"], {}), **record}
        passages = {**self.passages, **{passage["passage_id"]: passage for passage in passage_records(tool, result)}}
        return Provenance(transactions=transactions, passages=passages)


def transaction_records(tool: str, result: dict[str, Any]) -> list[dict[str, Any]]:
    if tool in TRANSACTION_TOOLS:
        return list(result["transactions"])
    if tool == "trace_funds":
        return [hop for path in result["paths"] for hop in path["hops"]]
    return []


def passage_records(tool: str, result: dict[str, Any]) -> list[dict[str, Any]]:
    if tool == "search_regulations":
        return list(result["passages"])
    if tool == "get_regulation_passage":
        return [result]
    return []


class Verification(BaseModel):
    status: Literal["passed", "failed"]
    errors: list[str]


def verify(decision: Decision, provenance: Provenance, as_of: datetime) -> Verification:
    cited = cited_txn_ids(decision)
    errors = [
        *([] if cited else ["decision cites no transactions"]),
        *transaction_errors(cited, provenance, as_of),
        *passage_errors(decision, provenance),
        *fact_errors(decision, provenance),
        *number_errors(decision),
        *typology_errors(decision),
    ]
    return Verification(status="failed" if errors else "passed", errors=errors)


def cited_txn_ids(decision: Decision) -> list[int]:
    cited: dict[int, None] = {}
    for claim in decision.claims:
        cited.update(dict.fromkeys(claim.txn_ids))
        cited.update(dict.fromkeys(fact.txn_id for fact in claim.facts))
    for verdict in decision.typologies:
        cited.update(dict.fromkeys(verdict.txn_ids))
    return list(cited)


def transaction_errors(txn_ids: Iterable[int], provenance: Provenance, as_of: datetime) -> list[str]:
    errors = []
    for txn_id in txn_ids:
        txn = provenance.transactions.get(txn_id)
        if txn is None:
            errors.append(f"transaction {txn_id} was not returned by any tool")
        elif datetime.fromisoformat(txn["ts"]) > as_of:
            errors.append(f"transaction {txn_id} is after as_of {as_of.isoformat()}")
    return errors


def passage_errors(decision: Decision, provenance: Provenance) -> list[str]:
    errors = []
    for passage_id in dict.fromkeys(passage_id for claim in decision.claims for passage_id in claim.passage_ids):
        passage = provenance.passages.get(passage_id)
        if passage is None:
            errors.append(f"passage {passage_id} was not returned by any tool")
        elif hashlib.sha256(passage["text"].encode()).hexdigest() != passage["content_sha256"]:
            errors.append(f"passage {passage_id} text does not match its content_sha256")
    return errors


def fact_errors(decision: Decision, provenance: Provenance) -> list[str]:
    errors = []
    for claim in decision.claims:
        for fact in claim.facts:
            txn = provenance.transactions.get(fact.txn_id)
            if txn is None:
                continue
            if ("ts" if fact.field == "date" else fact.field) not in txn:
                errors.append(f"fact {fact.field} of transaction {fact.txn_id} was never returned by any tool")
            elif not fact_holds(fact, txn):
                errors.append(
                    f"fact {fact.field}={fact.value} does not match transaction {fact.txn_id}, "
                    f"whose {fact.field} is {tool_value(fact, txn)}"
                )
    return errors


def fact_holds(fact: Fact, txn: dict[str, Any]) -> bool:
    if fact.field in ("date", "paid_currency", "received_currency"):
        return fact.value == tool_value(fact, txn)
    asserted = as_decimal(fact.value)
    return asserted is not None and abs(asserted - Decimal(str(txn[fact.field]))) <= AMOUNT_TOLERANCE


def tool_value(fact: Fact, txn: dict[str, Any]) -> str:
    if fact.field == "date":
        return datetime.fromisoformat(txn["ts"]).date().isoformat()
    return str(txn[fact.field])


def number_errors(decision: Decision) -> list[str]:
    errors: list[str] = []
    for claim in decision.claims:
        backed = {number for fact in claim.facts for number in numbers_in(fact.value)}
        errors.extend(
            f"number {number} in claim {claim.text!r} is not backed by a fact"
            for number in sorted(numbers_in(claim.text) - backed)
        )
    return errors


def numbers_in(text: str) -> set[Decimal]:
    return {Decimal(match.replace(",", "")) for match in NUMBER.findall(text)}


def typology_errors(decision: Decision) -> list[str]:
    return [
        f"typology {verdict.typology} is supported without citing a transaction"
        for verdict in decision.typologies
        if verdict.verdict == "supported" and not verdict.txn_ids
    ]


def as_decimal(value: str) -> Decimal | None:
    try:
        parsed = Decimal(value.replace(",", ""))
    except InvalidOperation:
        return None
    return parsed if parsed.is_finite() else None
