from datetime import datetime
from decimal import Decimal
from typing import Any

import pytest

from aml_investigator.investigation.decision import Claim, Decision, Fact, FactField, TypologyVerdict
from aml_investigator.investigation.verify import Provenance, numbers_in, verify

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
PROVENANCE = Provenance(transactions={11: TXN, 12: LATE_TXN}, passages={"AMLR-77-1-b": {"text": "record"}})


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
    ],
)
def test_rule3_matching_fact_passes(field: FactField, value: str) -> None:
    facts = [Fact(txn_id=11, field=field, value=value)]

    assert errors_of(decision(what=[claim(facts=facts)])) == []


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("amount_paid", "2848.98"),
        ("amount_paid", "about three thousand"),
        ("amount_paid", "NaN"),
        ("amount_received", "2848.96"),
        ("paid_currency", "US Dollar"),
        ("received_currency", "Euro"),
        ("date", "2022-09-03"),
    ],
)
def test_rule3_mismatching_fact_fails(field: FactField, value: str) -> None:
    facts = [Fact(txn_id=11, field=field, value=value)]

    assert errors_of(decision(what=[claim(facts=facts)])) == [f"fact {field}={value} does not match transaction 11"]


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
        "number 7 in claim 'Paid 2848.96 Euro to 7 counterparties.' is not backed by a fact"
    ]


def test_rule4_facts_of_other_claims_do_not_count() -> None:
    first = claim(text="Paid in Euro.", facts=[Fact(txn_id=11, field="amount_paid", value="2848.96")])
    second = claim(text="Paid 2848.96 in total.")

    assert errors_of(decision(what=[first, second])) == [
        "number 2848.96 in claim 'Paid 2848.96 in total.' is not backed by a fact"
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
