import json
from collections import Counter
from datetime import date, datetime
from pathlib import Path
from typing import Any

import pytest

from aml_investigator.core.settings import Settings
from aml_investigator.evaluation.injections import ATTACKS, Candidate, choose, create_injection_set
from aml_investigator.evaluation.samples import AlertKey, create_sample, load_sample
from tests.conftest import Database
from tests.test_evaluation import VAL_DAY, add_labelled_alert, settings_of

ACCOUNTS = 24
POSITIVES = 12
SAMPLE_SIZE = 6
SIZE = 8


def base_id(account: str) -> int:
    return 1000 + 10 * int(account.removeprefix("V"))


@pytest.fixture
def settings(db: Database) -> Settings:
    with db.owner() as conn:
        conn.execute("INSERT INTO core.accounts VALUES ('S', 1, 'Bank', 'ES', 'Corporation')")
        rows: list[tuple[Any, ...]] = [(1, datetime(2022, 9, 1), "S", "S"), (2, datetime(2022, 9, 10), "S", "S")]
        for i in range(ACCOUNTS):
            account = f"V{i:02}"
            add_labelled_alert(conn, account, VAL_DAY, escalate=i < POSITIVES, typologies=[])
            base = base_id(account)
            rows += [
                (base, datetime(2022, 9, 4, 10), account, "S"),
                (base + 1, datetime(2022, 9, 6, 10), "S", account),
                (base + 2, datetime(2022, 9, 5, 12), account, "S"),
                (base + 3, datetime(2022, 9, 5, 12), account, "S"),
                (base + 4, datetime(2022, 9, 7), account, "S"),
            ]
        with conn.cursor() as cursor:
            cursor.executemany(
                "INSERT INTO core.transactions VALUES (%s, %s, %s, %s, 100, 'Euro', 100, 'Euro', 'ACH', 100)", rows
            )
    return settings_of(db)


def make_set(settings: Settings, workspace: Path, *, size: int = SIZE, seed: int = 1) -> dict[str, Any]:
    if not (workspace / "eval" / "samples").exists():
        create_sample(settings, split="val", size=SAMPLE_SIZE, seed=3, test_reason=None, workspace=workspace)
    path = create_injection_set(settings, size=size, seed=seed, workspace=workspace)
    assert path == workspace / "eval" / "injections" / f"{settings.database}.json"
    document: dict[str, Any] = json.loads(path.read_text())
    return document


def keys_of(document: dict[str, Any]) -> list[AlertKey]:
    return [AlertKey(alert["account_id"], date.fromisoformat(alert["alert_date"])) for alert in document["alerts"]]


def test_excludes_val_sample_alerts(settings: Settings, tmp_path: Path) -> None:
    document = make_set(settings, tmp_path)

    sampled = set(load_sample(tmp_path / "eval" / "samples" / settings.database / "val.json").keys)
    assert len(sampled) == SAMPLE_SIZE
    assert len(keys_of(document)) == SIZE
    assert not sampled & set(keys_of(document))


def test_is_balanced_across_truth_and_attacks(settings: Settings, tmp_path: Path) -> None:
    document = make_set(settings, tmp_path)

    alerts = document["alerts"]
    assert Counter(alert["stratum"] for alert in alerts) == {"positive": SIZE // 2, "negative": SIZE // 2}
    assert Counter(alert["attack"] for alert in alerts) == dict.fromkeys(ATTACKS, SIZE // len(ATTACKS))
    for stratum in ("positive", "negative"):
        assert {alert["attack"] for alert in alerts if alert["stratum"] == stratum} == set(ATTACKS)
    positives = {f"V{i:02}" for i in range(POSITIVES)}
    assert all((alert["account_id"] in positives) == (alert["stratum"] == "positive") for alert in alerts)
    assert all(alert["text"] == ATTACKS[alert["attack"]] for alert in alerts)


def test_targets_the_transaction_listed_first_inside_the_alert_window(settings: Settings, tmp_path: Path) -> None:
    document = make_set(settings, tmp_path)

    assert {alert["txn_id"] for alert in document["alerts"]} == {
        base_id(alert["account_id"]) + 2 for alert in document["alerts"]
    }


def test_is_deterministic_for_a_seed(settings: Settings, tmp_path: Path) -> None:
    first = make_set(settings, tmp_path / "a", seed=5)
    again = make_set(settings, tmp_path / "b", seed=5)
    other = make_set(settings, tmp_path / "c", seed=6)

    assert first["seed"] == 5
    assert first["alerts"] == again["alerts"]
    assert keys_of(first) != keys_of(other)


def test_rows_mirror_the_document_and_are_rewritten_on_rerun(db: Database, settings: Settings, tmp_path: Path) -> None:
    def rows() -> list[tuple[object, ...]]:
        with db.owner() as conn:
            return conn.execute(
                "SELECT injection_id, txn_id, attack, text FROM core.reference_injections ORDER BY injection_id"
            ).fetchall()

    first = make_set(settings, tmp_path, seed=1)
    assert rows() == [
        (f"inj-{index:03d}", alert["txn_id"], alert["attack"], alert["text"])
        for index, alert in enumerate(first["alerts"])
    ]

    second = make_set(settings, tmp_path, size=4, seed=2)
    assert rows() == [
        (f"inj-{index:03d}", alert["txn_id"], alert["attack"], alert["text"])
        for index, alert in enumerate(second["alerts"])
    ]
    assert len(rows()) == 4


def test_fails_when_a_stratum_has_too_few_alerts(settings: Settings, tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="need"):
        make_set(settings, tmp_path, size=2 * POSITIVES)


def candidate(account: str, day: int, *, positive: bool, txn_id: int, counterparty: str = "S") -> Candidate:
    window_end = datetime(2022, 9, day)
    return Candidate(
        AlertKey(account, window_end.date()),
        datetime(2022, 9, day - 2),
        window_end,
        positive,
        txn_id,
        datetime(2022, 9, day - 1, 10),
        (account, counterparty),
    )


def test_alerts_whose_target_shows_in_another_chosen_window_are_not_both_chosen() -> None:
    same_account = [candidate("X", 7, positive=True, txn_id=1), candidate("X", 8, positive=False, txn_id=1)]
    counterparty = [
        candidate("X", 7, positive=True, txn_id=1, counterparty="Y"),
        candidate("Y", 7, positive=False, txn_id=2, counterparty="X"),
    ]
    independent = [candidate("X", 7, positive=True, txn_id=1), candidate("Y", 7, positive=False, txn_id=2)]

    with pytest.raises(ValueError, match="need"):
        choose(same_account, size=2, seed=1)
    with pytest.raises(ValueError, match="need"):
        choose(counterparty, size=2, seed=1)
    assert choose(independent, size=2, seed=1) == independent
