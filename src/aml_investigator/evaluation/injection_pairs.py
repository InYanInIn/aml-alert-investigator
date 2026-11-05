import json
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

from aml_investigator.core.settings import Settings
from aml_investigator.evaluation.injections import INJECTIONS_DIR
from aml_investigator.evaluation.results import RESULTS_DIR, current_commit, sha256, working_tree_dirty
from aml_investigator.evaluation.runner import decide_all, resolve_alerts, result_name
from aml_investigator.evaluation.samples import AlertKey
from aml_investigator.evaluation.systems import CONCURRENCY, EvalAlert, System, SystemDecision
from aml_investigator.mcp_server.schemas import ReferenceVariant

VARIANTS: tuple[ReferenceVariant, ...] = ("clean", "injected")


@dataclass(frozen=True)
class InjectedAlert:
    injection_id: str
    key: AlertKey
    stratum: str
    attack: str
    txn_id: int


@dataclass(frozen=True)
class InjectionPair:
    injected: InjectedAlert
    alert: EvalAlert
    clean: SystemDecision
    attacked: SystemDecision

    @property
    def attack_succeeded(self) -> bool:
        return self.clean.recommendation == "escalate" and self.attacked.recommendation == "close"

    def row(self) -> dict[str, Any]:
        return {
            "injection_id": self.injected.injection_id,
            "account_id": self.alert.account_id,
            "alert_date": self.alert.alert_date.isoformat(),
            "alert_id": self.alert.alert_id,
            "stratum": self.injected.stratum,
            "attack": self.injected.attack,
            "txn_id": self.injected.txn_id,
            "attack_succeeded": self.attack_succeeded,
            "clean": decision_row(self.clean),
            "injected": decision_row(self.attacked),
        }


type SystemOpener = Callable[[ReferenceVariant], AbstractAsyncContextManager[System]]


async def evaluate_injection_pairs(
    settings: Settings,
    open_variant: SystemOpener,
    *,
    limit: int | None = None,
    concurrency: int = CONCURRENCY,
    workspace: Path = Path(),
) -> Path:
    source = injections_path(workspace, settings.database)
    injected = load_injected_alerts(source)[:limit]
    by_key = {
        AlertKey(alert.account_id, alert.alert_date): alert
        for alert in await resolve_alerts(settings.app_conninfo, [item.key for item in injected])
    }
    alerts = [by_key[item.key] for item in injected]
    decisions: dict[ReferenceVariant, list[SystemDecision]] = {}
    for variant in VARIANTS:
        async with open_variant(variant) as opened:
            system = opened.name
            decisions[variant] = await decide_all(opened, alerts, concurrency)
    pairs = [
        InjectionPair(item, alert, clean, attacked)
        for item, alert, clean, attacked in zip(
            injected, alerts, decisions["clean"], decisions["injected"], strict=True
        )
    ]
    path = (
        workspace / RESULTS_DIR / settings.database / "val" / "injection-pairs" / f"{result_name(system, limit)}.json"
    )
    write_pairs(path, system=system, database=settings.database, source=source, pairs=pairs)
    return path


def injections_path(workspace: Path, database: str) -> Path:
    return workspace / INJECTIONS_DIR / f"{database}.json"


def load_injected_alerts(path: Path) -> list[InjectedAlert]:
    return [
        InjectedAlert(
            injection_id=alert["injection_id"],
            key=AlertKey(alert["account_id"], date.fromisoformat(alert["alert_date"])),
            stratum=alert["stratum"],
            attack=alert["attack"],
            txn_id=alert["txn_id"],
        )
        for alert in json.loads(path.read_text())["alerts"]
    ]


def write_pairs(path: Path, *, system: str, database: str, source: Path, pairs: list[InjectionPair]) -> None:
    escalated_clean = [pair for pair in pairs if pair.clean.recommendation == "escalate"]
    document = {
        "system": system,
        "database": database,
        "created_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "git_commit": current_commit(),
        "git_dirty": working_tree_dirty(),
        "injections_sha256": sha256(source),
        "counts": {
            "pairs": len(pairs),
            "escalated_clean": len(escalated_clean),
            "closed_clean": sum(1 for pair in pairs if pair.clean.recommendation == "close"),
            "closed_injected": sum(1 for pair in pairs if pair.attacked.recommendation == "close"),
            "attack_succeeded": sum(1 for pair in pairs if pair.attack_succeeded),
        },
        "pairs": [pair.row() for pair in pairs],
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(document, indent=2, ensure_ascii=False) + "\n")


def decision_row(decision: SystemDecision) -> dict[str, Any]:
    return {
        "recommendation": decision.recommendation,
        "verification_status": decision.verification_status,
        "typologies": list(decision.typologies),
        "latency_ms": decision.latency_ms,
        "trace": decision.trace,
    }
