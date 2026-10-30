from typing import Literal

from pydantic import BaseModel

Typology = Literal[
    "fan_out",
    "fan_in",
    "gather_scatter",
    "scatter_gather",
    "cycle",
    "random",
    "bipartite",
    "stack",
    "structuring",
    "profile_deviation",
]

FactField = Literal["amount_paid", "amount_received", "paid_currency", "received_currency", "date"]


class Fact(BaseModel):
    txn_id: int
    field: FactField
    value: str


class Claim(BaseModel):
    text: str
    txn_ids: list[int]
    passage_ids: list[str]
    facts: list[Fact]


class TypologyVerdict(BaseModel):
    typology: Typology
    verdict: Literal["supported", "not_supported", "insufficient_evidence"]
    txn_ids: list[int]


class Decision(BaseModel):
    recommendation: Literal["close", "escalate"]
    typologies: list[TypologyVerdict]
    who: list[Claim]
    what: list[Claim]
    when: list[Claim]
    where: list[Claim]
    why: list[Claim]
    how: list[Claim]

    @property
    def claims(self) -> list[Claim]:
        return [*self.who, *self.what, *self.when, *self.where, *self.why, *self.how]


class AnalystReview(BaseModel):
    analyst: str
    final_decision: Literal["close", "escalate"]
    comment: str = ""
