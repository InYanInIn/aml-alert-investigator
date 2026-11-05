import asyncio
import time
from collections.abc import Sequence
from dataclasses import replace
from pathlib import Path

import psycopg
from psycopg.rows import class_row

from aml_investigator.core.errors import NotFoundError
from aml_investigator.core.settings import Settings
from aml_investigator.core.sql import load
from aml_investigator.evaluation.baselines import TRAINED_SYSTEMS, open_system
from aml_investigator.evaluation.baselines.runtime import RuntimeSettings
from aml_investigator.evaluation.metrics import (
    OperatingPoint,
    ScoredAlert,
    choose_operating_point,
    decide_at,
    report,
    stratum_weights,
)
from aml_investigator.evaluation.openings import record_opening, require_reason
from aml_investigator.evaluation.results import (
    AlertTruth,
    Evaluated,
    EvaluationResult,
    results_path,
    validation_operating_point,
    write_result,
)
from aml_investigator.evaluation.samples import AlertKey, EvalSplit, Strata, load_sample, sample_path
from aml_investigator.evaluation.systems import EvalAlert, Recommendation, System, SystemDecision
from aml_investigator.evaluation.training import read_train_labels

CONCURRENCY = 8

RESOLVE_ALERTS_SQL = load(__package__, "resolve_alerts")
SAMPLE_TRUTH_SQL = load(__package__, "sample_truth")


async def evaluate(
    settings: Settings, *, system: str, split: EvalSplit, test_reason: str | None, workspace: Path = Path()
) -> EvaluationResult:
    require_reason(split, test_reason)
    train_labels = read_train_labels(settings.evaluator_conninfo) if system in TRAINED_SYSTEMS else None
    async with open_system(system, RuntimeSettings.of(settings), train_labels=train_labels) as opened:
        return await evaluate_system(settings, opened, split=split, test_reason=test_reason, workspace=workspace)


async def evaluate_system(
    settings: Settings, system: System, *, split: EvalSplit, test_reason: str | None, workspace: Path = Path()
) -> EvaluationResult:
    require_reason(split, test_reason)
    sample = load_sample(sample_path(workspace, settings.database, split))
    alerts = await resolve_alerts(settings.app_conninfo, sample.keys)
    decisions = await decide_all(system, alerts)
    reused_point = (
        validation_operating_point(workspace, settings.database, system.name)
        if split == "test" and any(decision.recommendation is None for decision in decisions)
        else None
    )
    truths = await read_truth(settings.evaluator_conninfo, alerts)
    if test_reason is not None:
        record_test_evaluation(workspace, settings.database, system.name, len(alerts), test_reason)
    point = reused_point or fitted_operating_point(decisions, truths, sample.population)
    evaluated = [
        Evaluated(alert, decision, final_recommendation(decision, point), truth)
        for alert, decision, truth in zip(alerts, decisions, truths, strict=True)
    ]
    result = EvaluationResult(
        system=system.name,
        split=split,
        database=settings.database,
        alerts=len(alerts),
        operating_point=point,
        metrics=report([e.outcome() for e in evaluated], sample.population.by_truth()),
        path=results_path(workspace, settings.database, split, system.name),
    )
    write_result(result, sample=sample, evaluated=evaluated, workspace=workspace)
    return result


async def resolve_alerts(conninfo: str, keys: Sequence[AlertKey]) -> list[EvalAlert]:
    parameters = {
        "account_ids": [key.account_id for key in keys],
        "alert_dates": [key.alert_date for key in keys],
    }
    async with await psycopg.AsyncConnection.connect(conninfo) as conn:
        cursor = conn.cursor(row_factory=class_row(EvalAlert))
        await cursor.execute(RESOLVE_ALERTS_SQL, parameters)
        alerts = await cursor.fetchall()
    missing = sorted(set(keys) - {AlertKey(alert.account_id, alert.alert_date) for alert in alerts})
    if missing:
        raise NotFoundError("sample alerts", [f"{key.account_id}@{key.alert_date}" for key in missing])
    return alerts


async def decide_all(system: System, alerts: Sequence[EvalAlert]) -> list[SystemDecision]:
    gate = asyncio.Semaphore(CONCURRENCY)

    async def timed_decision(alert: EvalAlert) -> SystemDecision:
        async with gate:
            started = time.perf_counter()
            decision = await system.decide(alert)
            elapsed_ms = (time.perf_counter() - started) * 1000
        return decision if decision.latency_ms is not None else replace(decision, latency_ms=elapsed_ms)

    return list(await asyncio.gather(*(timed_decision(alert) for alert in alerts)))


async def read_truth(conninfo: str, alerts: Sequence[EvalAlert]) -> list[AlertTruth]:
    async with await psycopg.AsyncConnection.connect(conninfo) as conn:
        cursor = conn.cursor(row_factory=class_row(AlertTruth))
        await cursor.execute(SAMPLE_TRUTH_SQL, {"alert_ids": [alert.alert_id for alert in alerts]})
        by_alert = {truth.alert_id: truth for truth in await cursor.fetchall()}
    unlabelled = [alert.alert_id for alert in alerts if alert.alert_id not in by_alert]
    if unlabelled:
        raise NotFoundError("truth for alerts", unlabelled)
    return [by_alert[alert.alert_id] for alert in alerts]


def fitted_operating_point(
    decisions: Sequence[SystemDecision], truths: Sequence[AlertTruth], population: Strata
) -> OperatingPoint | None:
    if all(decision.recommendation is not None for decision in decisions):
        return None
    weights = stratum_weights(population.by_truth(), [truth.escalate_truth for truth in truths])
    return choose_operating_point(scored_alerts(decisions, truths), weights)


def scored_alerts(decisions: Sequence[SystemDecision], truths: Sequence[AlertTruth]) -> list[ScoredAlert]:
    return [
        ScoredAlert(required_score(decision), truth.escalate_truth)
        for decision, truth in zip(decisions, truths, strict=True)
        if decision.recommendation is None
    ]


def required_score(decision: SystemDecision) -> float:
    if decision.risk_score is None:
        raise ValueError("a decision without a recommendation needs a risk_score")
    return decision.risk_score


def final_recommendation(decision: SystemDecision, point: OperatingPoint | None) -> Recommendation:
    if decision.recommendation is not None:
        return decision.recommendation
    if point is None:
        raise ValueError("a decision without a recommendation needs an operating point")
    return decide_at(point, required_score(decision))


def record_test_evaluation(workspace: Path, database: str, system: str, alerts: int, reason: str) -> None:
    record_opening(
        workspace,
        computed=f"{database}: {system} evaluated on the test sample ({alerts} alerts); metrics with intervals.",
        reason=reason,
    )
