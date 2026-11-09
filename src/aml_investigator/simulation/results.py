import hashlib
import json
import statistics
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from aml_investigator.core.errors import DomainError
from aml_investigator.evaluation.metrics import Outcome, interval, measure
from aml_investigator.evaluation.report import NOT_RUN, RESULTS_DOCUMENT, load, sources_lines, table
from aml_investigator.evaluation.results import RESULTS_DIR, THRESHOLDS_DIR, GitState, sha256
from aml_investigator.investigation.agent.conversation import PROFILE_PROMPT, PROMPT_VERSION
from aml_investigator.simulation import MAX_INVESTIGATIONS
from aml_investigator.simulation.laundering import GroupProgress
from aml_investigator.simulation.loop import AGENT_SYSTEMS, Case, SimulationRun
from aml_investigator.simulation.segments import segments_path
from aml_investigator.simulation.world import LIVE_DAYS, live_days

HELD_OUT_DATABASE = "aml_sim_heldout"
DEVELOPMENT_DATABASE = "aml_sim_dev"
SPREAD_DATABASE = "aml_sim_spread"
REPORT_START = "<!-- sim-report:start -->"
REPORT_END = "<!-- sim-report:end -->"
SECTION_HEADING = "## M9 — behaviour simulator"
MISSING = "-"
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
    if system == "azure_profile":
        return PROFILE_PROMPT.version
    return PROMPT_VERSION if system in AGENT_SYSTEMS else None


def thresholds_digest(database: str) -> str:
    files = (THRESHOLDS_DIR / f"{database}.json", segments_path(database))
    return hashlib.sha256("".join(sha256(path) for path in files).encode()).hexdigest()


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
        "thresholds_sha256": thresholds_digest(run.database),
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


type Document = dict[str, Any]


def update_simulation_report(workspace: Path) -> Path:
    path = workspace / RESULTS_DOCUMENT
    text = path.read_text()
    if REPORT_START not in text:
        text = f"{text.rstrip()}\n\n{SECTION_HEADING}\n\n{REPORT_START}\n{REPORT_END}\n"
    before, _, rest = text.partition(REPORT_START)
    _, _, after = rest.partition(REPORT_END)
    path.write_text(f"{before}{REPORT_START}\n{render_simulation(workspace)}\n{REPORT_END}{after}")
    return path


def render_simulation(workspace: Path) -> str:
    return "\n\n".join(
        (
            "Generated by `aml sim report` from the committed files under `eval/results/aml_sim_*/sim/`; edit the "
            "commentary outside these markers. This is a stress test on synthetic behaviour; it does not estimate "
            "accuracy on real bank data. Each agent run has about 50 alerts and 6-8 groups, so agent results are "
            "counts without intervals. Partial runs (`--limit`) are not tabulated here.",
            world_section(workspace, DEVELOPMENT_DATABASE),
            world_section(workspace, HELD_OUT_DATABASE),
            spread_section(workspace),
        )
    )


def full_runs(workspace: Path, database: str) -> dict[Path, Document]:
    paths = sorted((workspace / RESULTS_DIR / database / "sim").glob("*.json"))
    documents = {path: load(path) or {} for path in paths}
    return {path: document for path, document in documents.items() if document["limit"] == MAX_INVESTIGATIONS}


def label(document: Document) -> str:
    truncated = f", truncated at {document['last_day']}" if document["truncated"] else ""
    return f"{document['system']}, seed {document['seed']}, cadence {document['cadence']}{truncated}"


def number(value: float | None, digits: int = 3) -> str:
    return "n/a" if value is None else f"{value:.{digits}f}"


def median_days(document: Document) -> float | None:
    days = [group["days_to_first_block"] for group in document["groups"] if group["days_to_first_block"] is not None]
    return statistics.median(days) if days else None


def sink_total(document: Document, field: str) -> float:
    return float(sum(group[field] for group in document["groups"]))


def world_section(workspace: Path, database: str) -> str:
    runs = full_runs(workspace, database)
    if not runs:
        return f"### {database}\n\n{NOT_RUN}"
    documents = list(runs.values())
    return "\n\n".join(
        (
            f"### {database}",
            summary_table(documents),
            scenario_table(documents),
            persona_table(documents),
            workload_table(documents),
            shadow_table(documents),
            sources_lines(workspace, runs),
        )
    )


def summary_table(documents: Sequence[Document]) -> str:
    rows = [
        [
            label(document),
            f"{document['counts']['groups_found']} / {document['counts']['groups']}",
            number(median_days(document), 1),
            f"{sink_total(document, 'sink_eur'):,.0f} / {sink_total(document, 'planned_sink_eur'):,.0f}",
            str(document["metrics"]["alerts"]),
            number(document["metrics"]["close_rate"]),
            number(document["metrics"]["false_close_rate"]),
            number(document["metrics"]["escalation_precision"]),
            number(document["metrics"]["mean_cost_usd"], 4),
            number(document["metrics"]["mean_latency_ms"], 1),
        ]
        for document in documents
    ]
    header = [
        "Run",
        "Groups found",
        "Median days to first block",
        "EUR to sink / planned",
        "Alerts",
        "Close rate",
        "False-close rate",
        "Escalation precision",
        "Mean cost per alert (USD)",
        "Mean latency (ms)",
    ]
    return table(header, rows)


def scenario_cell(group: Document) -> str:
    money = f"{group['sink_eur']:,.0f} of {group['planned_sink_eur']:,.0f} EUR"
    if not group["found"]:
        return f"not found, {money}"
    return f"found after {group['days_to_first_block']} days, {money}"


def scenario_table(documents: Sequence[Document]) -> str:
    scenarios = sorted(
        {(group["scenario"], group["typology"]) for document in documents for group in document["groups"]}
    )
    by_run = [{group["scenario"]: group for group in document["groups"]} for document in documents]
    rows = [
        [scenario, typology, *(scenario_cell(groups[scenario]) if scenario in groups else MISSING for groups in by_run)]
        for scenario, typology in scenarios
    ]
    return "#### Per scenario\n\n" + table(["Scenario", "Typology", *map(label, documents)], rows)


def persona_table(documents: Sequence[Document]) -> str:
    rows = [
        [
            label(document),
            persona,
            str(values["alerts"]),
            number(values["close_rate"]),
            number(values["false_close_rate"]),
            number(values["escalation_precision"]),
        ]
        for document in documents
        for persona, values in document["by_persona"].items()
    ]
    return "#### Per persona\n\n" + table(
        ["Run", "Persona", "Alerts", "Close rate", "False-close rate", "Escalation precision"], rows
    )


def workload_table(documents: Sequence[Document]) -> str:
    rows = [
        [label(document), *(f"{week['alerts']} / {week['escalations']}" for week in document["workload"])]
        for document in documents
    ]
    weeks = [f"Week {number_of_week} alerts / escalations" for number_of_week in range(1, ROUNDS + 1)]
    return "#### Analyst workload\n\n" + table(["Run", *weeks], rows)


def shadow_table(documents: Sequence[Document]) -> str:
    agents = [document for document in documents if document["shadow"] is not None]
    if not agents:
        return "#### Agent against its shadow B4\n\nNo agent run."
    columns = (
        ("alerts_compared", "Compared"),
        ("alerts_differing", "Differing"),
        ("laundering_caught_b4_closed", "Laundering caught that B4 closed"),
        ("honest_closed_b4_escalated", "Honest customers closed that B4 escalated"),
        ("laundering_closed_b4_escalated", "Laundering closed that B4 escalated"),
        ("honest_escalated_b4_closed", "Honest customers escalated that B4 closed"),
    )
    rows = [[label(document), *(str(document["shadow"][name]) for name, _ in columns)] for document in agents]
    return "#### Agent against its shadow B4\n\n" + table(["Run", *(heading for _, heading in columns)], rows)


def percentile_cell(values: Iterable[float | None]) -> str:
    present = [value for value in values if value is not None]
    if not present:
        return "n/a"
    low, high = interval(present)
    return f"{statistics.median(present):.2f} [{number(low, 2)}, {number(high, 2)}]"


def sink_share(document: Document) -> float | None:
    planned = sink_total(document, "planned_sink_eur")
    return sink_total(document, "sink_eur") / planned if planned else None


def spread_section(workspace: Path) -> str:
    runs = full_runs(workspace, SPREAD_DATABASE)
    if not runs:
        return f"### Spread over seeds\n\n{NOT_RUN}"
    documents = list(runs.values())
    keys = sorted({(document["system"], document["cadence"]) for document in documents})
    rows = []
    for system, cadence in keys:
        seeds = [d for d in documents if (d["system"], d["cadence"]) == (system, cadence)]
        rows.append(
            [
                system,
                str(cadence),
                str(len(seeds)),
                percentile_cell(d["counts"]["groups_found"] for d in seeds),
                percentile_cell(median_days(d) for d in seeds),
                percentile_cell(sink_share(d) for d in seeds),
                percentile_cell(d["metrics"]["close_rate"] for d in seeds),
                percentile_cell(d["metrics"]["false_close_rate"] for d in seeds),
            ]
        )
    header = [
        "System",
        "Cadence",
        "Seeds",
        "Groups found",
        "Median days to first block",
        "Share of planned EUR reaching the sink",
        "Close rate",
        "False-close rate",
    ]
    return (
        "### Spread over seeds\n\nMedian with the 2.5th and 97.5th percentile over the seeds.\n\n"
        f"{table(header, rows)}\n\n{sources_lines(workspace, runs)}"
    )
