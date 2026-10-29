import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Literal

from pydantic import BaseModel

from aml_investigator.agent.decision import Decision, Fact

AMOUNT_TOLERANCE = Decimal("0.01")
NUMBER = re.compile(r"(?<!\w)\d+(?:,\d{3})*(?:\.\d+)?(?!\w)")


@dataclass(frozen=True)
class Provenance:
    transactions: dict[int, dict[str, Any]]
    passages: dict[str, dict[str, Any]] = field(default_factory=dict)


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
    return [
        f"passage {passage_id} was not returned by any tool"
        for claim in decision.claims
        for passage_id in claim.passage_ids
        if passage_id not in provenance.passages
    ]


def fact_errors(decision: Decision, provenance: Provenance) -> list[str]:
    errors = []
    for claim in decision.claims:
        for fact in claim.facts:
            txn = provenance.transactions.get(fact.txn_id)
            if txn is not None and not fact_holds(fact, txn):
                errors.append(f"fact {fact.field}={fact.value} does not match transaction {fact.txn_id}")
    return errors


def fact_holds(fact: Fact, txn: dict[str, Any]) -> bool:
    if fact.field == "date":
        return fact.value == datetime.fromisoformat(txn["ts"]).date().isoformat()
    if fact.field in ("paid_currency", "received_currency"):
        return fact.value == str(txn[fact.field])
    asserted = as_decimal(fact.value)
    return asserted is not None and abs(asserted - Decimal(str(txn[fact.field]))) <= AMOUNT_TOLERANCE


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
