import hashlib
import json
import subprocess
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from aml_investigator.evaluation.metrics import (
    BOOTSTRAP_RESAMPLES,
    BOOTSTRAP_SEED,
    Estimate,
    MetricReport,
    OperatingPoint,
    Outcome,
)
from aml_investigator.evaluation.samples import EvalSplit, Sample, sample_path
from aml_investigator.evaluation.systems import EvalAlert, Recommendation, SystemDecision

RESULTS_DIR = Path("eval/results")
THRESHOLDS_DIR = Path("config/thresholds")
RUN_INPUTS = (":/src", ":/config", ":/pyproject.toml", ":/uv.lock", ":/eval/samples")


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
            "trace": self.decision.trace,
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


def validation_operating_point(workspace: Path, database: str, system: str) -> OperatingPoint:
    val_result = results_path(workspace, database, "val", system)
    if not val_result.exists():
        raise FileNotFoundError(f"{val_result} is missing; run the validation split first to fix the operating point")
    stored = json.loads(val_result.read_text())["operating_point"]
    if stored is None:
        raise ValueError(f"{val_result} has no operating point")
    return OperatingPoint(**stored)


def write_result(result: EvaluationResult, *, sample: Sample, evaluated: Sequence[Evaluated], workspace: Path) -> None:
    recommendations = [e.recommendation for e in evaluated]
    costs = [e.decision.cost_usd for e in evaluated if e.decision.cost_usd is not None]
    document = {
        "system": result.system,
        "split": sample.split,
        "database": sample.database,
        "created_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "git_commit": current_commit(),
        "git_dirty": working_tree_dirty(),
        "sample_sha256": sha256(sample_path(workspace, result.database, result.split)),
        "thresholds_sha256": sha256(workspace / THRESHOLDS_DIR / f"{result.database}.json"),
        "operating_point": asdict(result.operating_point) if result.operating_point else None,
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
            "weighted": estimates_document(result.metrics.weighted),
            "unweighted": estimates_document(result.metrics.unweighted),
        },
        "alerts": [e.row() for e in evaluated],
    }
    result.path.parent.mkdir(parents=True, exist_ok=True)
    result.path.write_text(json.dumps(document, indent=2) + "\n")


def estimates_document(estimates: dict[str, Estimate]) -> dict[str, dict[str, float | None]]:
    return {name: asdict(estimate) for name, estimate in estimates.items()}


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def current_commit() -> str:
    return git("rev-parse", "HEAD")


def working_tree_dirty() -> bool:
    return git("status", "--porcelain", "--", *RUN_INPUTS) != ""


def git(*arguments: str) -> str:
    completed = subprocess.run(  # noqa: S603
        ["git", *arguments],  # noqa: S607
        capture_output=True,
        text=True,
        check=True,
        cwd=Path(__file__).parent,
    )
    return completed.stdout.strip()
