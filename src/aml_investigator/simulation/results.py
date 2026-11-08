import json
from collections.abc import Mapping, Sequence
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from aml_investigator.core.errors import DomainError
from aml_investigator.evaluation.metrics import Outcome, measure
from aml_investigator.evaluation.results import RESULTS_DIR, THRESHOLDS_DIR, GitState, sha256
from aml_investigator.investigation.agent.conversation import PROMPT_VERSION
from aml_investigator.simulation import MAX_INVESTIGATIONS
from aml_investigator.simulation.laundering import GroupProgress
from aml_investigator.simulation.loop import AGENT_SYSTEMS, Case, SimulationRun
from aml_investigator.simulation.world import LIVE_DAYS, live_days

HELD_OUT_DATABASE = "aml_sim_heldout"
DAYS_PER_ROUND = 7
ROUNDS = LIVE_DAYS // DAYS_PER_ROUND
RATE_NAMES = ("close_rate", "false_close_rate", "escalation_precision")


class HeldOutAlreadyOpenedError(DomainError):
    def __init__(self, path: Path) -> None:
        super().__init__(
            f"{path} exists; the held-out world is opened once per agent, delete the file by hand to rerun"
        )


def result_path(database: str, seed: int, system: str, cadence: int, limit: int) -> Path:
    partial = "" if limit == MAX_INVESTIGATIONS else f"-first{limit}"
    return RESULTS_DIR / database / "sim" / f"seed{seed}-{system}-cadence{cadence}{partial}.json"


def refuse_second_opening(database: str, system: str) -> None:
    if database != HELD_OUT_DATABASE or system not in AGENT_SYSTEMS:
        return
    opened = sorted((RESULTS_DIR / database / "sim").glob(f"seed[0-9]*-{system}-cadence*.json"))
    if opened:
        raise HeldOutAlreadyOpenedError(opened[0])


def outcome(case: Case) -> Outcome:
    return Outcome(
        escalate_truth=case.truth.escalate_truth,
        truth_typologies=tuple(case.truth.typologies),
        recommendation=case.recommendation,
        typologies=case.decision.typologies,
        cost_usd=case.decision.cost_usd,
        latency_ms=case.decision.latency_ms,
    )


def measure_cases(cases: Sequence[Case]) -> dict[str, Any]:
    return measure([outcome(case) for case in cases], [1.0] * len(cases))


def rates(cases: Sequence[Case], measured: Mapping[str, Any]) -> dict[str, Any]:
    return {"alerts": len(cases), **{name: measured[name] for name in RATE_NAMES}}


def overall_metrics(cases: Sequence[Case], measured: Mapping[str, Any]) -> dict[str, Any]:
    costs = [case.decision.cost_usd for case in cases if case.decision.cost_usd is not None]
    return {
        **rates(cases, measured),
        "mean_cost_usd": measured["mean_cost_usd"],
        "mean_latency_ms": measured["mean_latency_ms"],
        "total_cost_usd": sum(costs) if costs else None,
    }


def group_row(group: GroupProgress) -> dict[str, Any]:
    return {
        **asdict(group),
        "first_payment_day": iso(group.first_payment_day),
        "first_block_day": iso(group.first_block_day),
        "stopped_on": iso(group.stopped_on),
        "found": group.first_block_day is not None,
        "days_to_first_block": group.days_to_first_block,
    }


def iso(day: Any) -> str | None:
    return None if day is None else str(day.isoformat())


def round_of(case: Case) -> int:
    return (case.decided_on - live_days()[0]).days // DAYS_PER_ROUND + 1


def workload(cases: Sequence[Case]) -> list[dict[str, int]]:
    return [
        {
            "round": number,
            "alerts": sum(round_of(case) == number for case in cases),
            "escalations": sum(round_of(case) == number and case.recommendation == "escalate" for case in cases),
        }
        for number in range(1, ROUNDS + 1)
    ]


def shadow_comparison(cases: Sequence[Case]) -> dict[str, int] | None:
    paired = [case for case in cases if case.shadow is not None]
    if not paired:
        return None

    def count(recommendation: str, shadow: str, truth: bool) -> int:
        return sum(
            case.recommendation == recommendation and case.shadow == shadow and case.truth.escalate_truth == truth
            for case in paired
        )

    return {
        "alerts_compared": len(paired),
        "alerts_differing": sum(case.recommendation != case.shadow for case in paired),
        "laundering_caught_b4_closed": count("escalate", "close", truth=True),
        "honest_closed_b4_escalated": count("close", "escalate", truth=False),
        "laundering_closed_b4_escalated": count("close", "escalate", truth=True),
        "honest_escalated_b4_closed": count("escalate", "close", truth=False),
    }


def alert_row(case: Case) -> dict[str, Any]:
    return {
        "alert_id": case.alert.alert_id,
        "account_id": case.alert.account_id,
        "alert_date": case.alert.alert_date.isoformat(),
        "decided_on": case.decided_on.isoformat(),
        "persona": case.actor.persona,
        "group_id": case.actor.group_id,
        "scenario": case.actor.scenario,
        "escalate_truth": case.truth.escalate_truth,
        "truth_typologies": case.truth.typologies,
        "recommendation": case.recommendation,
        "system_recommendation": case.decision.recommendation,
        "shadow_b4": case.shadow,
        "risk_score": case.decision.risk_score,
        "typologies": list(case.decision.typologies),
        "verification_status": case.decision.verification_status,
        "cost_usd": case.decision.cost_usd,
        "latency_ms": case.decision.latency_ms,
        "trace": case.decision.trace,
    }


def prompt_version(system: str) -> str | None:
    return PROMPT_VERSION if system in AGENT_SYSTEMS else None


def result_document(run: SimulationRun, git_state: GitState) -> dict[str, Any]:
    personas = sorted({case.actor.persona for case in run.cases})
    return {
        "database": run.database,
        "seed": run.seed,
        "system": run.system,
        "cadence": run.cadence,
        "limit": run.limit,
        "truncated": run.truncated,
        "undecided_alerts": run.undecided_alerts,
        "last_day": run.last_day.isoformat(),
        "created_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "git_commit": git_state.commit,
        "git_dirty": git_state.dirty,
        "thresholds_sha256": sha256(THRESHOLDS_DIR / f"{run.database}.json"),
        "prompt_version": prompt_version(run.system),
        "transaction_sha256": run.tape_sha256,
        "counts": {
            "groups": len(run.groups),
            "groups_found": sum(group.first_block_day is not None for group in run.groups),
            "blocked_accounts": len(run.blocked),
        },
        "groups": [group_row(group) for group in run.groups],
        "blocked": {account: day.isoformat() for account, day in sorted(run.blocked.items())},
        "metrics": overall_metrics(run.cases, measure_cases(run.cases)),
        "by_persona": {
            persona: rates(subset, measure_cases(subset))
            for persona in personas
            if (subset := [case for case in run.cases if case.actor.persona == persona])
        },
        "workload": workload(run.cases),
        "shadow": shadow_comparison(run.cases),
        "alerts": [alert_row(case) for case in run.cases],
    }


def write_result(run: SimulationRun, git_state: GitState) -> Path:
    path = result_path(run.database, run.seed, run.system, run.cadence, run.limit)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(result_document(run, git_state), indent=2) + "\n")
    return path


def summary(document: Mapping[str, Any]) -> str:
    counts, metrics = document["counts"], document["metrics"]
    truncated = f", TRUNCATED with {document['undecided_alerts']} undecided" if document["truncated"] else ""
    return (
        f"{document['system']} on {document['database']} seed {document['seed']} cadence {document['cadence']}: "
        f"{counts['groups_found']} of {counts['groups']} groups found, {metrics['alerts']} alerts, "
        f"close rate {metrics['close_rate']}, false-close rate {metrics['false_close_rate']}{truncated}"
    )
