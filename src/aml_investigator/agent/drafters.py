from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any

from aml_investigator.agent.decision import Claim, Decision, Fact, TypologyVerdict


@dataclass(frozen=True)
class EvidencePack:
    alert: dict[str, Any]
    transactions: list[dict[str, Any]]


Drafter = Callable[[EvidencePack], Awaitable[Decision]]


async def scripted_drafter(evidence: EvidencePack) -> Decision:
    rule_hits = evidence.alert["rule_hits"]
    in_pack = {txn["txn_id"]: txn for txn in evidence.transactions}
    flagged = dict.fromkeys(txn_id for hit in rule_hits for txn_id in hit["evidence_txn_ids"])
    cited = [txn_id for txn_id in flagged if txn_id in in_pack]
    return Decision(
        recommendation="escalate" if rule_hits else "close",
        typologies=[
            TypologyVerdict(
                typology="fan_out", verdict="supported" if cited else "insufficient_evidence", txn_ids=cited
            )
        ],
        who=[],
        what=[payment_claim(in_pack[cited[0]], cited)] if cited else [],
        when=[],
        where=[],
        why=[],
        how=[],
    )


def payment_claim(txn: dict[str, Any], cited: list[int]) -> Claim:
    amount = Decimal(str(txn["amount_paid"]))
    day = datetime.fromisoformat(txn["ts"]).date().isoformat()
    return Claim(
        text=f"The account paid {amount:,.2f} {txn['paid_currency']} on {day}.",
        txn_ids=cited,
        passage_ids=[],
        facts=[
            Fact(txn_id=txn["txn_id"], field="amount_paid", value=f"{amount:.2f}"),
            Fact(txn_id=txn["txn_id"], field="paid_currency", value=txn["paid_currency"]),
            Fact(txn_id=txn["txn_id"], field="date", value=day),
        ],
    )
