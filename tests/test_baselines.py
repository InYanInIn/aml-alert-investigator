import json
from datetime import date, datetime, timedelta
from pathlib import Path

import psycopg
import pytest
from pydantic import SecretStr

from aml_investigator.core.db import open_read_only_pool
from aml_investigator.core.settings import Settings
from aml_investigator.evaluation.baselines import SYSTEM_NAMES, open_system
from aml_investigator.evaluation.baselines.b1 import open_rule_score
from aml_investigator.evaluation.baselines.b3 import GradientBoosting, TrainingSummary, alert_features
from aml_investigator.evaluation.baselines.runtime import RuntimeSettings
from aml_investigator.evaluation.systems import EvalAlert
from aml_investigator.evaluation.training import read_train_labels
from tests.conftest import Database

TRAIN_ALERTS_PER_CLASS = 24


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def settings_for(db: Database, evaluator_dsn: str | None = None) -> Settings:
    return Settings(
        owner_dsn=SecretStr(db.owner_dsn),
        mcp_dsn=SecretStr(db.mcp_dsn),
        app_dsn=SecretStr(db.app_dsn),
        evaluator_dsn=SecretStr(evaluator_dsn or db.evaluator_dsn),
    )


def runtime_for(db: Database) -> RuntimeSettings:
    return RuntimeSettings.of(settings_for(db))


def add_accounts(conn: psycopg.Connection[tuple[object, ...]], *accounts: str) -> None:
    for account in accounts:
        conn.execute(
            "INSERT INTO core.accounts VALUES (%s, 1, 'Bank', %s, 'Corporation') ON CONFLICT DO NOTHING",
            (account, account),
        )


def add_transaction(
    conn: psycopg.Connection[tuple[object, ...]],
    txn_id: int,
    ts: datetime,
    sender: str,
    receiver: str,
    amount: float,
    payment_format: str = "Cheque",
    received_currency: str = "Euro",
) -> None:
    conn.execute(
        "INSERT INTO core.transactions VALUES (%s, %s, %s, %s, %s, 'Euro', %s, %s, %s, %s)",
        (txn_id, ts, sender, receiver, amount, amount, received_currency, payment_format, amount),
    )


def add_alert(
    conn: psycopg.Connection[tuple[object, ...]], account: str, alert_date: date, hits: dict[str, float]
) -> EvalAlert:
    row = conn.execute(
        "INSERT INTO core.alerts (account_id, alert_date, window_start, window_end) "
        "VALUES (%s, %s, %s, %s) RETURNING alert_id",
        (account, alert_date, alert_date - timedelta(days=2), alert_date + timedelta(days=1)),
    ).fetchone()
    assert row is not None
    alert_id = int(str(row[0]))
    for rule_id, score in hits.items():
        conn.execute("INSERT INTO core.alert_rule_hits VALUES (%s, %s, %s, '{}')", (alert_id, rule_id, score))
    return EvalAlert(alert_id, account, alert_date)


def add_truth(conn: psycopg.Connection[tuple[object, ...]], alert: EvalAlert, escalate: bool) -> None:
    conn.execute("INSERT INTO truth.alert_truth VALUES (%s, %s, %s, '{}')", (alert.alert_id, escalate, escalate))


def at(alert_date: date, hour: int) -> datetime:
    return datetime(alert_date.year, alert_date.month, alert_date.day, hour)


@pytest.fixture
def learnable(db: Database) -> tuple[EvalAlert, EvalAlert]:
    txn_ids = iter(range(1, 10_000))
    with db.owner() as conn:
        add_accounts(conn, "C1", "C2", "C3")
        add_transaction(conn, next(txn_ids), datetime(2022, 9, 10, 12), "C1", "C2", 10.0)
        for index in range(2 * TRAIN_ALERTS_PER_CLASS):
            laundering = index % 2 == 0
            account = f"T{index}"
            alert_date = date(2022, 9, 1) + timedelta(days=index % 5)
            add_accounts(conn, account)
            for receiver in ("C1", "C2", "C3"):
                payment_format = "ACH" if laundering else "Cheque"
                add_transaction(conn, next(txn_ids), at(alert_date, 9), account, receiver, 500.0, payment_format)
            alert = add_alert(conn, account, alert_date, {"R2_fan_out": 3.0})
            add_truth(conn, alert, laundering)
        evaluated = []
        for account, payment_format in (("VA", "ACH"), ("VC", "Cheque")):
            add_accounts(conn, account)
            alert_date = date(2022, 9, 8)
            for receiver in ("C1", "C2", "C3"):
                add_transaction(conn, next(txn_ids), at(alert_date, 9), account, receiver, 500.0, payment_format)
            alert = add_alert(conn, account, alert_date, {"R2_fan_out": 3.0})
            add_truth(conn, alert, payment_format != "ACH")
            evaluated.append(alert)
    ach_alert, cheque_alert = evaluated
    return ach_alert, cheque_alert


@pytest.mark.anyio
async def test_b0_always_escalates(db: Database) -> None:
    async with open_system("b0", runtime_for(db)) as system:
        decision = await system.decide(EvalAlert(1, "A1", date(2022, 9, 2)))

    assert system.name == "b0"
    assert (decision.recommendation, decision.risk_score) == ("escalate", None)


@pytest.mark.anyio
async def test_b1_sums_scores_relative_to_thresholds(db: Database) -> None:
    with db.owner() as conn:
        add_accounts(conn, "A1")
        alert = add_alert(conn, "A1", date(2022, 9, 2), {"R2_fan_out": 22.0, "R5_structuring": 6.0})

    thresholds = {"R2_fan_out": 11.0, "R5_structuring": 4.0, "R1_fan_in": 13.0}
    async with open_rule_score(db.app_dsn, thresholds) as system:
        decision = await system.decide(alert)

    assert decision.recommendation is None
    assert decision.risk_score == pytest.approx(2.0 + 1.5)


@pytest.mark.anyio
async def test_b3_learns_the_signal_deterministically(db: Database, learnable: tuple[EvalAlert, EvalAlert]) -> None:
    ach_alert, cheque_alert = learnable
    scores = []
    for _ in range(2):
        labels = read_train_labels(db.evaluator_dsn)
        async with open_system("b3", runtime_for(db), train_labels=labels) as system:
            assert isinstance(system, GradientBoosting)
            assert system.training_summary == TrainingSummary(2 * TRAIN_ALERTS_PER_CLASS, TRAIN_ALERTS_PER_CLASS)
            ach, cheque = await system.decide(ach_alert), await system.decide(cheque_alert)
        assert (ach.recommendation, cheque.recommendation) == (None, None)
        scores.append((ach.risk_score, cheque.risk_score))

    [(ach_score, cheque_score), repeated] = scores
    assert ach_score is not None
    assert cheque_score is not None
    assert ach_score > 0.9
    assert cheque_score < 0.1
    assert repeated == (ach_score, cheque_score)


@pytest.mark.anyio
async def test_b3_features_ignore_transactions_from_window_end_on(db: Database) -> None:
    alert_date = date(2022, 9, 3)
    window_end = at(alert_date + timedelta(days=1), 0)
    with db.owner() as conn:
        add_accounts(conn, "F", "C1", "C2", "C3")
        add_transaction(conn, 1, datetime(2022, 8, 30, 0), "C1", "F", 100.0)
        add_transaction(conn, 2, at(alert_date, 10), "F", "C2", 80.0, "ACH")
        add_transaction(conn, 3, at(alert_date, 11), "F", "F", 5.0)
        alert = add_alert(conn, "F", alert_date, {"R3_pass_through": 80.0})

    async with open_read_only_pool(db.app_dsn) as pool:
        before = await alert_features(pool, [alert.alert_id])
        with db.owner() as conn:
            add_transaction(conn, 4, window_end, "C3", "F", 1_000.0, "Bitcoin", "US Dollar")
            add_transaction(conn, 5, window_end + timedelta(days=1), "F", "C3", 900.0, "Cash")
        after = await alert_features(pool, [alert.alert_id])

    features = dict(zip(before.names, before.values[0], strict=True))
    assert before.alert_ids == after.alert_ids == (alert.alert_id,)
    assert (before.values == after.values).all()
    assert features["out_count"] == 1
    assert features["in_count"] == 0
    assert features["out_eur"] == 80.0
    assert features["ach_share"] == pytest.approx(0.5)
    assert features["self_transfer_share"] == pytest.approx(0.5)
    assert features["account_age_days"] == pytest.approx(5.0)
    assert features["r3_pass_through_hit"] == 1
    assert features["r3_pass_through_score"] == 80.0
    assert features["rule_count"] == 1


def test_label_reader_reads_the_train_window_and_refuses_the_app_role(
    db: Database, learnable: tuple[EvalAlert, EvalAlert]
) -> None:
    labels = read_train_labels(db.evaluator_dsn)

    assert len(labels) == 2 * TRAIN_ALERTS_PER_CLASS
    assert not {alert.alert_id for alert in learnable} & labels.keys()
    with pytest.raises(psycopg.errors.InsufficientPrivilege, match="permission denied"):
        read_train_labels(db.app_dsn)


def test_b3_requires_train_labels(db: Database) -> None:
    with pytest.raises(ValueError, match="train_labels"):
        open_system("b3", runtime_for(db))


@pytest.mark.anyio
@pytest.mark.parametrize("name", SYSTEM_NAMES)
async def test_every_system_opens_without_a_working_evaluator_dsn(
    db: Database, learnable: tuple[EvalAlert, EvalAlert], name: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    labels = read_train_labels(db.evaluator_dsn)
    settings = settings_for(db, evaluator_dsn="host=invalid.invalid dbname=none user=none connect_timeout=1")
    runtime = RuntimeSettings.of(settings)
    thresholds = tmp_path / "config" / "thresholds" / f"{runtime.database}.json"
    thresholds.parent.mkdir(parents=True)
    thresholds.write_text(json.dumps({"thresholds": {"R2_fan_out": 11.0}}))
    monkeypatch.chdir(tmp_path)

    async with open_system(name, runtime, train_labels=labels) as system:
        decision = await system.decide(learnable[0])

    assert system.name == name
    assert decision.recommendation is not None or decision.risk_score is not None


@pytest.mark.anyio
async def test_scripted_runs_the_investigation_service(db: Database) -> None:
    with db.owner() as conn:
        add_accounts(conn, "A1", "A2", "A3")
        add_transaction(conn, 11, datetime(2022, 9, 2, 10, 15), "A1", "A2", 2848.96, "ACH")
        add_transaction(conn, 12, datetime(2022, 9, 2, 11, 0), "A1", "A3", 1500.0, "ACH")
        alert = add_alert(conn, "A1", date(2022, 9, 2), {})
        conn.execute(
            "INSERT INTO core.alert_rule_hits VALUES (%s, 'R2_fan_out', 2.0, ARRAY[11, 12])", (alert.alert_id,)
        )

    async with open_system("scripted", runtime_for(db)) as system:
        decision = await system.decide(alert)

    assert (decision.recommendation, decision.verification_status) == ("escalate", "passed")
    assert decision.typologies == ("fan_out",)
    with db.owner() as conn:
        assert conn.execute("SELECT alert_id FROM review.investigations").fetchall() == [(alert.alert_id,)]


def test_unknown_system_lists_the_valid_names(db: Database) -> None:
    with pytest.raises(ValueError, match="b0, b1, b3, b3_no_age, scripted"):
        open_system("b2", runtime_for(db))
