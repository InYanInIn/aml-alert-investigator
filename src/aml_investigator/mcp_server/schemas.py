from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel

from aml_investigator.monitoring.typologies import Typology

ReferenceVariant = Literal["clean", "injected"]
PaymentDirection = Literal["in", "out", "both"]
TraceDirection = Literal["forward", "backward"]


class RuleHit(BaseModel):
    rule_id: str
    score: float
    evidence_txn_ids: list[int]


class Alert(BaseModel):
    alert_id: int
    account_id: str
    alert_date: date
    window_start: datetime
    window_end: datetime
    as_of: datetime
    rule_hits: list[RuleHit]


class Transaction(BaseModel):
    txn_id: int
    ts: datetime
    from_account: str
    to_account: str
    amount_paid: float
    paid_currency: str
    amount_received: float
    received_currency: str
    payment_format: str
    reference_untrusted: str | None


class TransactionPage(BaseModel):
    as_of: datetime
    truncated: bool
    transactions: list[Transaction]


class TransactionSelection(BaseModel):
    as_of: datetime
    omitted: int
    transactions: list[Transaction]


class PaymentFormatShare(BaseModel):
    payment_format: str
    share: float


class OtherAccounts(BaseModel):
    count: int
    account_ids: list[str]


class AccountProfile(BaseModel):
    as_of: datetime
    account_id: str
    entity_type: str
    first_seen: datetime | None
    active_days: int
    incoming_count: int
    incoming_eur: float
    outgoing_count: int
    outgoing_eur: float
    self_transfer_count: int
    distinct_counterparties: int
    payment_format_shares: list[PaymentFormatShare]
    outgoing_days: int
    daily_outgoing_eur_mean: float | None
    daily_outgoing_eur_median: float | None
    other_accounts_of_entity: OtherAccounts


class Counterparty(BaseModel):
    account_id: str
    incoming_count: int
    incoming_eur: float
    outgoing_count: int
    outgoing_eur: float
    total_eur: float
    first_ts: datetime
    last_ts: datetime
    payment_formats: list[str]


class CounterpartyPage(BaseModel):
    as_of: datetime
    counterparty_count: int
    truncated: bool
    counterparties: list[Counterparty]


class Hop(BaseModel):
    txn_id: int
    ts: datetime
    from_account: str
    to_account: str
    amount_eur: float


class FundPath(BaseModel):
    flow_eur: float
    hops: list[Hop]


class FundTrace(BaseModel):
    as_of: datetime
    direction: TraceDirection
    max_hops: int
    truncated: bool
    paths: list[FundPath]


class TypologyCandidate(BaseModel):
    rule_id: str
    score: float
    evidence_txn_ids: list[int]
    typologies: list[Typology]


class TypologyCandidates(BaseModel):
    alert_id: int
    candidates: list[TypologyCandidate]


class PassageMatch(BaseModel):
    passage_id: str
    doc_id: str
    article: str
    paragraph: str | None
    heading: str | None
    text: str
    content_sha256: str
    rank: float


class RegulationMatches(BaseModel):
    query: str
    passages: list[PassageMatch]


class RegulationPassage(BaseModel):
    passage_id: str
    doc_id: str
    article: str
    paragraph: str | None
    heading: str | None
    text: str
    content_sha256: str
