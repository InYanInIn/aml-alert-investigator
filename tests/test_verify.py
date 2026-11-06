import hashlib
from datetime import datetime
from decimal import Decimal
from typing import Any

import pytest

from aml_investigator.investigation.decision import Claim, Decision, Fact, FactField, TypologyVerdict
from aml_investigator.investigation.verify import (
    Provenance,
    number_errors,
    numbers_in,
    verify,
    with_canonical_passage_ids,
)

AS_OF = datetime(2022, 9, 3)
TXN: dict[str, Any] = {
    "txn_id": 11,
    "ts": "2022-09-02T10:15:00",
    "from_account": "A1",
    "to_account": "A2",
    "amount_paid": 2848.96,
    "paid_currency": "Euro",
    "amount_received": 3100.5,
    "received_currency": "US Dollar",
    "payment_format": "ACH",
}
LATE_TXN: dict[str, Any] = {**TXN, "txn_id": 12, "ts": "2022-09-03T00:00:01"}
HOP: dict[str, Any] = {
    "txn_id": 21,
    "ts": "2022-09-02T12:00:00",
    "from_account": "A2",
    "to_account": "A4",
    "amount_eur": 2800.0,
}
PASSAGE: dict[str, Any] = {
    "passage_id": "AMLR-77-1-b",
    "text": "record",
    "content_sha256": hashlib.sha256(b"record").hexdigest(),
}
PROVENANCE = Provenance(transactions={11: TXN, 12: LATE_TXN, 21: HOP}, passages={"AMLR-77-1-b": PASSAGE})


def claim(text: str = "Paid to a new counterparty.", **overrides: Any) -> Claim:
    fields: dict[str, Any] = {"text": text, "txn_ids": [11], "passage_ids": [], "facts": [], **overrides}
    return Claim(**fields)


def decision(
    what: list[Claim] | None = None, typologies: list[TypologyVerdict] | None = None, **overrides: Any
) -> Decision:
    fields: dict[str, Any] = {
        "recommendation": "escalate",
        "typologies": typologies if typologies is not None else [],
        "who": [],
        "what": what if what is not None else [claim()],
        "when": [],
        "where": [],
        "why": [],
        "how": [],
        **overrides,
    }
    return Decision(**fields)


def errors_of(candidate: Decision) -> list[str]:
    return verify(candidate, PROVENANCE, AS_OF).errors


def test_valid_decision_passes() -> None:
    result = verify(decision(), PROVENANCE, AS_OF)

    assert result.status == "passed"
    assert result.errors == []


def test_decision_without_transaction_citations_fails() -> None:
    result = verify(decision(what=[claim(txn_ids=[])]), PROVENANCE, AS_OF)

    assert result.status == "failed"
    assert result.errors == ["decision cites no transactions"]


def test_rule1_transaction_not_returned_by_tools_fails() -> None:
    assert errors_of(decision(what=[claim(txn_ids=[11, 99])])) == ["transaction 99 was not returned by any tool"]


def test_rule1_transaction_after_as_of_fails() -> None:
    assert errors_of(decision(what=[claim(txn_ids=[12])])) == ["transaction 12 is after as_of 2022-09-03T00:00:00"]


def test_rule1_transaction_at_as_of_passes() -> None:
    at_as_of = Provenance(transactions={11: {**TXN, "ts": "2022-09-03T00:00:00"}})

    assert verify(decision(), at_as_of, AS_OF).status == "passed"


def test_rule1_applies_to_typology_citations() -> None:
    typologies = [TypologyVerdict(typology="fan_out", verdict="supported", txn_ids=[99])]

    assert errors_of(decision(typologies=typologies)) == ["transaction 99 was not returned by any tool"]


def test_rule2_known_passage_passes() -> None:
    assert errors_of(decision(what=[claim(passage_ids=["AMLR-77-1-b"])])) == []


def test_rule2_passage_whose_text_does_not_match_its_hash_fails() -> None:
    altered = Provenance(transactions={11: TXN}, passages={"AMLR-77-1-b": {**PASSAGE, "text": "recorded"}})

    assert verify(decision(what=[claim(passage_ids=["AMLR-77-1-b"])]), altered, AS_OF).errors == [
        "passage AMLR-77-1-b text does not match its content_sha256"
    ]


def test_rule2_unknown_passage_fails() -> None:
    assert errors_of(decision(what=[claim(passage_ids=["AMLR-99"])])) == [
        "passage AMLR-99 was not returned by any tool"
    ]


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("amount_paid", "2848.96"),
        ("amount_paid", "2848.97"),
        ("amount_paid", "2,848.95"),
        ("amount_received", "3100.50"),
        ("paid_currency", "Euro"),
        ("received_currency", "US Dollar"),
        ("date", "2022-09-02"),
        ("date", "2022-09-02T10:15:00"),
    ],
)
def test_rule3_matching_fact_passes(field: FactField, value: str) -> None:
    facts = [Fact(txn_id=11, field=field, value=value)]

    assert errors_of(decision(what=[claim(facts=facts)])) == []


@pytest.mark.parametrize(
    ("field", "value", "returned"),
    [
        ("amount_paid", "2848.98", "2848.96"),
        ("amount_paid", "about three thousand", "2848.96"),
        ("amount_paid", "NaN", "2848.96"),
        ("amount_paid", "2848.96 Euro", "2848.96"),
        ("amount_received", "2848.96", "3100.5"),
        ("paid_currency", "US Dollar", "Euro"),
        ("received_currency", "Euro", "US Dollar"),
        ("date", "2022-09-03", "2022-09-02"),
    ],
)
def test_rule3_mismatching_fact_fails_and_names_the_returned_value(field: FactField, value: str, returned: str) -> None:
    facts = [Fact(txn_id=11, field=field, value=value)]

    assert errors_of(decision(what=[claim(facts=facts)])) == [
        f"fact {field}={value} does not match transaction 11, whose {field} is {returned}"
    ]


@pytest.mark.parametrize("field", ["amount_paid", "paid_currency", "amount_received", "received_currency"])
def test_rule3_fact_about_a_field_no_tool_returned_fails(field: FactField) -> None:
    facts = [Fact(txn_id=21, field=field, value="2800.00")]

    assert errors_of(decision(what=[claim(txn_ids=[21], facts=facts)])) == [
        f"fact {field} of transaction 21 was never returned by any tool"
    ]


def test_rule3_date_of_a_trace_hop_is_checked() -> None:
    facts = [Fact(txn_id=21, field="date", value="2022-09-02")]

    assert errors_of(decision(what=[claim(txn_ids=[21], facts=facts)])) == []


def test_rule3_fact_about_unknown_transaction_fails_as_uncited() -> None:
    facts = [Fact(txn_id=99, field="paid_currency", value="Euro")]

    assert errors_of(decision(what=[claim(facts=facts)])) == ["transaction 99 was not returned by any tool"]


def test_rule4_numbers_backed_by_facts_pass() -> None:
    backed = claim(
        text="Paid 2,848.96 Euro on 2022-09-02.",
        facts=[
            Fact(txn_id=11, field="amount_paid", value="2848.96"),
            Fact(txn_id=11, field="date", value="2022-09-02"),
        ],
    )

    assert errors_of(decision(what=[backed])) == []


def test_rule4_unbacked_number_fails() -> None:
    unbacked = claim(
        text="Paid 2848.96 Euro to 7 counterparties.", facts=[Fact(txn_id=11, field="amount_paid", value="2848.96")]
    )

    assert errors_of(decision(what=[unbacked])) == [
        "number 7 in claim 'Paid 2848.96 Euro to 7 counterparties.' is not backed by a fact or a cited id"
    ]


def test_rule4_numbers_of_the_claims_cited_ids_are_backed() -> None:
    cited = claim(
        text="Transaction 11 breaches AMLR art. 77(1).", txn_ids=[11], passage_ids=["AMLR art. 77(1)"], facts=[]
    )

    assert number_errors(decision(what=[cited]), PROVENANCE, AS_OF) == []


def test_rule4_timestamp_date_fact_backs_day_and_hour() -> None:
    stamped = claim(
        text="Paid on 2022-09-02 at 10:15.", facts=[Fact(txn_id=11, field="date", value="2022-09-02T10:15:00")]
    )

    assert errors_of(decision(what=[stamped])) == []


def test_rule4_cited_transaction_id_does_not_back_an_amount() -> None:
    other = {**TXN, "txn_id": 12000, "amount_paid": 5.0}
    lookalike = claim(text="Account paid 12,000.00 US Dollar in transaction 12000.", txn_ids=[12000])

    result = verify(decision(what=[lookalike]), Provenance(transactions={12000: other}), AS_OF)

    assert result.errors == [
        "number 12000.00 in claim 'Account paid 12,000.00 US Dollar in transaction 12000.' "
        "is not backed by a fact or a cited id"
    ]


def test_rule4_cited_passage_id_does_not_back_a_count() -> None:
    lookalike = claim(text="26 transfers under AMLR art. 26(1)", passage_ids=["AMLR art. 26(1)"])
    provenance = Provenance(
        transactions={11: TXN},
        passages={"AMLR art. 26(1)": {**PASSAGE, "passage_id": "AMLR art. 26(1)"}},
    )

    assert verify(decision(what=[lookalike]), provenance, AS_OF).errors == [
        "number 26 in claim '26 transfers under AMLR art. 26(1)' is not backed by a fact or a cited id"
    ]


def test_rule4_facts_of_other_claims_do_not_count() -> None:
    first = claim(text="Paid in Euro.", facts=[Fact(txn_id=11, field="amount_paid", value="2848.96")])
    second = claim(text="Paid 2848.96 in total.")

    assert errors_of(decision(what=[first, second])) == [
        "number 2848.96 in claim 'Paid 2848.96 in total.' is not backed by a fact or a cited id"
    ]


@pytest.mark.parametrize(
    ("text", "numbers"),
    [
        ("2,848.96", {"2848.96"}),
        ("2848.96", {"2848.96"}),
        ("paid 1,000,000 and 12.50.", {"1000000", "12.5"}),
        ("on 2022-09-02", {"2022", "9", "2"}),
        ("account 8000EBD30 and A1", set()),
        ("no numbers here", set()),
    ],
)
def test_number_normalisation(text: str, numbers: set[str]) -> None:
    assert numbers_in(text) == {Decimal(number) for number in numbers}


def test_rule5_supported_typology_with_transactions_passes() -> None:
    typologies = [TypologyVerdict(typology="fan_out", verdict="supported", txn_ids=[11])]

    assert errors_of(decision(typologies=typologies)) == []


def test_rule5_supported_typology_without_transactions_fails() -> None:
    typologies = [
        TypologyVerdict(typology="fan_out", verdict="supported", txn_ids=[]),
        TypologyVerdict(typology="cycle", verdict="insufficient_evidence", txn_ids=[]),
    ]

    assert errors_of(decision(typologies=typologies)) == ["typology fan_out is supported without citing a transaction"]


def test_provenance_merges_full_records_with_trace_hops() -> None:
    hop = {**HOP, "txn_id": 11, "amount_eur": 2848.96}
    trace = {"paths": [{"flow_eur": 2848.96, "hops": [hop, HOP]}]}

    seen = Provenance().including("trace_funds", trace).including("list_transactions", {"transactions": [TXN]})

    assert seen.transactions[11] == {**TXN, "amount_eur": 2848.96}
    assert seen.transactions[21] == HOP
    assert seen.passages == {}


def test_provenance_keeps_passages_from_search_and_lookup() -> None:
    other = {**PASSAGE, "passage_id": "PL-74-1"}

    seen = (
        Provenance()
        .including("search_regulations", {"query": "record", "passages": [{**PASSAGE, "rank": 0.1}]})
        .including("get_regulation_passage", other)
        .including("get_account_profile", {"account_id": "A1"})
    )

    assert set(seen.passages) == {"AMLR-77-1-b", "PL-74-1"}
    assert seen.transactions == {}


SPACED = "AMLR art. 26(1)"
THRESHOLD_TEXT = "Transactions above EUR 10 000 or 1,500 require checks."
THRESHOLD_PASSAGE: dict[str, Any] = {
    "passage_id": SPACED,
    "text": THRESHOLD_TEXT,
    "content_sha256": hashlib.sha256(THRESHOLD_TEXT.encode()).hexdigest(),
}
THRESHOLD_PROVENANCE = Provenance(transactions={11: TXN}, passages={SPACED: THRESHOLD_PASSAGE})


def test_whitespace_variant_passage_id_is_canonicalised_and_passes() -> None:
    cited = decision(what=[claim(text="See AMLR art.26(1).", passage_ids=["AMLR art.26(1)"])])

    canonical = with_canonical_passage_ids(cited, THRESHOLD_PROVENANCE)

    assert canonical.what[0].passage_ids == [SPACED]
    assert canonical.what[0].text == "See AMLR art. 26(1)."
    assert verify(canonical, THRESHOLD_PROVENANCE, AS_OF).errors == []


def test_unknown_passage_id_is_left_alone_and_still_fails() -> None:
    cited = decision(what=[claim(passage_ids=["AMLR art.99(1)"])])

    canonical = with_canonical_passage_ids(cited, THRESHOLD_PROVENANCE)

    assert canonical.what[0].passage_ids == ["AMLR art.99(1)"]
    assert verify(canonical, THRESHOLD_PROVENANCE, AS_OF).errors == [
        "passage AMLR art.99(1) was not returned by any tool"
    ]


@pytest.mark.parametrize("amount", ["10 000", "10,000", "10000", "1,500"])
def test_rule4_number_from_cited_passage_text_passes(amount: str) -> None:
    threshold = claim(text=f"Above EUR {amount}.", passage_ids=[SPACED])

    assert verify(decision(what=[threshold]), THRESHOLD_PROVENANCE, AS_OF).errors == []


def test_rule4_number_from_passage_the_claim_does_not_cite_fails() -> None:
    threshold = claim(text="Above EUR 10000.")

    assert verify(decision(what=[threshold]), THRESHOLD_PROVENANCE, AS_OF).errors == [
        "number 10000 in claim 'Above EUR 10000.' is not backed by a fact or a cited id"
    ]


def test_rule4_as_of_components_are_backed() -> None:
    stamped = claim(text="As of 2022-09-03 00:00 and 9 3.")

    assert verify(decision(what=[stamped]), THRESHOLD_PROVENANCE, datetime(2022, 9, 3)).errors == []


def test_rule4_count_is_still_unbacked() -> None:
    counted = claim(text="38 incoming payments.", passage_ids=[SPACED])

    assert verify(decision(what=[counted]), THRESHOLD_PROVENANCE, AS_OF).errors == [
        "number 38 in claim '38 incoming payments.' is not backed by a fact or a cited id"
    ]
