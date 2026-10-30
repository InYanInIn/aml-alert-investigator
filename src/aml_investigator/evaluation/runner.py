import asyncio
import hashlib
import json
import subprocess
import time
from collections.abc import Sequence
from dataclasses import asdict, dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import psycopg
from psycopg.rows import class_row

from aml_investigator.core.errors import NotFoundError
from aml_investigator.core.settings import Settings
from aml_investigator.core.sql import load
from aml_investigator.evaluation.baselines import TRAINED_SYSTEMS, open_system
from aml_investigator.evaluation.baselines.runtime import RuntimeSettings
from aml_investigator.evaluation.metrics import (
    BOOTSTRAP_RESAMPLES,
    BOOTSTRAP_SEED,
    Estimate,
    MetricReport,
    OperatingPoint,
    Outcome,
    ScoredAlert,
    choose_operating_point,
    decide_at,
    report,
    stratum_weights,
)
from aml_investigator.evaluation.openings import record_opening, require_reason
from aml_investigator.evaluation.samples import AlertKey, EvalSplit, Sample, load_sample, sample_path
from aml_investigator.evaluation.systems import EvalAlert, Recommendation, System, SystemDecision
from aml_investigator.evaluation.training import read_train_labels

CONCURRENCY = 8
RESULTS_DIR = Path("eval/results")
THRESHOLDS_DIR = Path("config/thresholds")

RESOLVE_ALERTS_SQL = load(__package__, "resolve_alerts")
SAMPLE_TRUTH_SQL = load(__package__, "sample_truth")


@dataclass(frozen=True)
class AlertTruth:
    alert_id: int
    escalate_truth: bool
    typologies: list[str]


@dataclass(frozen=True)
class Evaluated:
    alert: EvalAlert
    decision: SystemDecision
    recommendation: Recommendation
    truth: AlertTruth

    def outcome(self) -> Outcome:
        return Outcome(
            escalate_truth=self.truth.escalate_truth,
            truth_typologies=tuple(self.truth.typologies),
            recommendation=self.recommendation,
            typologies=self.decision.typologies,
            cost_usd=self.decision.cost_usd,
            latency_ms=self.decision.latency_ms,
        )

    def row(self) -> dict[str, Any]:
        return {
            "account_id": self.alert.account_id,
            "alert_date": self.alert.alert_date.isoformat(),
            "alert_id": self.alert.alert_id,
            "recommendation": self.recommendation,
            "system_recommendation": self.decision.recommendation,
            "risk_score": self.decision.risk_score,
            "typologies": list(self.decision.typologies),
            "verification_status": self.decision.verification_status,
            "cost_usd": self.decision.cost_usd,
            "latency_ms": self.decision.latency_ms,
            "escalate_truth": self.truth.escalate_truth,
            "truth_typologies": self.truth.typologies,
        }


@dataclass(frozen=True)
class EvaluationResult:
    system: str
    split: EvalSplit
    database: str
    alerts: int
    operating_point: OperatingPoint | None
    metrics: MetricReport
    path: Path

    def summary(self) -> str:
        point = f", close when risk_score < {self.operating_point.threshold:.4f}" if self.operating_point else ""
        lines = [
            f"{self.system} on {self.split} ({self.database}): {self.alerts} alerts{point}",
            f"{'metric':<22} {'weighted [95% CI]':<30} unweighted",
        ]
        for name, weighted in self.metrics.weighted.items():
            interval = f"{number(weighted.value)} [{number(weighted.low)}, {number(weighted.high)}]"
            lines.append(f"{name:<22} {interval:<30} {number(self.metrics.unweighted[name].value)}")
        lines.append(f"written to {self.path}")
        return "\n".join(lines)


def number(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.3f}"


def results_path(workspace: Path, database: str, split: EvalSplit, system: str) -> Path:
    return workspace / RESULTS_DIR / database / split / f"{system}.json"


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
    sample_file = sample_path(workspace, settings.database, split)
    sample = load_sample(sample_file)
    alerts = await resolve_alerts(settings.app_conninfo, sample.keys)
    decisions = await decide_all(system, alerts)
    scored = any(decision.recommendation is None for decision in decisions)
    stored_point = (
        stored_operating_point(results_path(workspace, settings.database, "val", system.name))
        if scored and split == "test"
        else None
    )
    truths = await read_truth(settings.evaluator_conninfo, alerts)
    if test_reason is not None:
        record_test_evaluation(workspace, settings.database, system.name, len(alerts), test_reason)
    weights = stratum_weights(sample.population.by_truth(), [truth.escalate_truth for truth in truths])
    point = stored_point or (choose_operating_point(scored_alerts(decisions, truths), weights) if scored else None)
    evaluated = [
        Evaluated(alert, decision, final_recommendation(decision, point), truth)
        for alert, decision, truth in zip(alerts, decisions, truths, strict=True)
    ]
    metrics = report([e.outcome() for e in evaluated], sample.population.by_truth())
    path = results_path(workspace, settings.database, split, system.name)
    write_result(
        path,
        system=system.name,
        sample=sample,
        sample_file=sample_file,
        thresholds_file=workspace / THRESHOLDS_DIR / f"{settings.database}.json",
        point=point,
        metrics=metrics,
        evaluated=evaluated,
    )
    return EvaluationResult(system.name, split, settings.database, len(alerts), point, metrics, path)


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


def stored_operating_point(val_result: Path) -> OperatingPoint:
    if not val_result.exists():
        raise FileNotFoundError(f"{val_result} is missing; run the validation split first to fix the operating point")
    stored = json.loads(val_result.read_text())["operating_point"]
    if stored is None:
        raise ValueError(f"{val_result} has no operating point")
    return OperatingPoint(**stored)


def record_test_evaluation(workspace: Path, database: str, system: str, alerts: int, reason: str) -> None:
    record_opening(
        workspace,
        computed=f"{database}: {system} evaluated on the test sample ({alerts} alerts); metrics with intervals.",
        reason=reason,
    )


def write_result(
    path: Path,
    *,
    system: str,
    sample: Sample,
    sample_file: Path,
    thresholds_file: Path,
    point: OperatingPoint | None,
    metrics: MetricReport,
    evaluated: Sequence[Evaluated],
) -> None:
    recommendations = [e.recommendation for e in evaluated]
    costs = [e.decision.cost_usd for e in evaluated if e.decision.cost_usd is not None]
    document = {
        "system": system,
        "split": sample.split,
        "database": sample.database,
        "created_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "git_commit": current_commit(),
        "git_dirty": working_tree_dirty(),
        "sample_sha256": sha256(sample_file),
        "thresholds_sha256": sha256(thresholds_file),
        "operating_point": asdict(point) if point else None,
        "bootstrap": {"resamples": BOOTSTRAP_RESAMPLES, "seed": BOOTSTRAP_SEED},
        "counts": {
            "alerts": len(evaluated),
            "population": asdict(sample.population),
            "sample": {
                "positive": sum(1 for e in evaluated if e.truth.escalate_truth),
                "negative": sum(1 for e in evaluated if not e.truth.escalate_truth),
            },
            "recommended_close": recommendations.count("close"),
            "recommended_escalate": recommendations.count("escalate"),
            "total_cost_usd": sum(costs) if costs else None,
        },
        "metrics": {
            "weighted": estimates_document(metrics.weighted),
            "unweighted": estimates_document(metrics.unweighted),
        },
        "alerts": [e.row() for e in evaluated],
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(document, indent=2) + "\n")


def estimates_document(estimates: dict[str, Estimate]) -> dict[str, dict[str, float | None]]:
    return {name: asdict(estimate) for name, estimate in estimates.items()}


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def current_commit() -> str:
    return git("rev-parse", "HEAD")


def working_tree_dirty() -> bool:
    return git("status", "--porcelain") != ""


def git(*arguments: str) -> str:
    completed = subprocess.run(  # noqa: S603
        ["git", *arguments],  # noqa: S607
        capture_output=True,
        text=True,
        check=True,
        cwd=Path(__file__).parent,
    )
    return completed.stdout.strip()
