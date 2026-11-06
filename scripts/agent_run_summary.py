import argparse
import json
import statistics
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any

PARSE_ERROR_PREFIX = "output does not parse into a Decision"
UNWEIGHTED_NOTE = "unweighted, not comparable to baselines"


@dataclass(frozen=True)
class RunSetSummary:
    title: str
    investigations: int
    without_trace: int
    loop_calls: Counter[str]
    loop_calls_valid: int
    loop_stops: Counter[str]
    draft_attempts: int
    drafts_parsed: int
    repaired: int
    passed_first: int
    passed_after_repair: int
    failed_final: int
    tokens: list[int]
    latency_minutes: list[float]
    closed: int
    positives: int | None
    positives_closed: int
    negatives: int | None
    negatives_closed: int


def share(part: int, whole: int) -> str:
    return f"{part}/{whole} ({part / whole:.0%})" if whole else "n/a"


def median_and_max(values: list[int] | list[float], digits: int) -> tuple[str, str]:
    if not values:
        return "n/a", "n/a"
    return f"{statistics.median(values):.{digits}f}", f"{max(values):.{digits}f}"


def summarize(title: str, runs: list[dict[str, Any]]) -> RunSetSummary:
    traced = [run for run in runs if run.get("trace") is not None]
    traces = [run["trace"] for run in traced]
    loop_calls = [call for trace in traces for call in trace["tool_calls"] if call["phase"] == "loop"]
    attempts = [attempt for trace in traces for attempt in trace["verifications"]]
    first_passed = [bool(t["verifications"]) and t["verifications"][0]["status"] == "passed" for t in traces]
    final_passed = [bool(t["verifications"]) and t["verifications"][-1]["status"] == "passed" for t in traces]
    labelled = [run for run in traced if "escalate_truth" in run]
    positives = [run for run in labelled if run["escalate_truth"]]
    negatives = [run for run in labelled if not run["escalate_truth"]]
    return RunSetSummary(
        title=title,
        investigations=len(traced),
        without_trace=len(runs) - len(traced),
        loop_calls=Counter(call["tool"] for call in loop_calls),
        loop_calls_valid=sum(1 for call in loop_calls if "error" not in call),
        loop_stops=Counter(trace["loop_stop"] for trace in traces),
        draft_attempts=len(attempts),
        drafts_parsed=sum(1 for a in attempts if not any(e.startswith(PARSE_ERROR_PREFIX) for e in a["errors"])),
        repaired=sum(1 for trace in traces if trace["repairs"] > 0),
        passed_first=sum(first_passed),
        passed_after_repair=sum(
            1 for first, final in zip(first_passed, final_passed, strict=True) if final and not first
        ),
        failed_final=final_passed.count(False),
        tokens=[trace["tokens"] for trace in traces],
        latency_minutes=[run["latency_ms"] / 60_000 for run in traced],
        closed=sum(1 for run in traced if run["recommendation"] == "close"),
        positives=len(positives) if labelled else None,
        positives_closed=sum(1 for run in positives if run["recommendation"] == "close"),
        negatives=len(negatives) if labelled else None,
        negatives_closed=sum(1 for run in negatives if run["recommendation"] == "close"),
    )


def render(summary: RunSetSummary) -> list[str]:
    n = summary.investigations
    total_calls = sum(summary.loop_calls.values())
    token_median, token_max = median_and_max(summary.tokens, 0)
    latency_median, latency_max = median_and_max(summary.latency_minutes, 1)
    by_tool = ", ".join(f"{tool} {count}" for tool, count in sorted(summary.loop_calls.items())) or "none"
    stops = ", ".join(f"{stop} {count}" for stop, count in sorted(summary.loop_stops.items())) or "none"
    lines = [
        f"### {summary.title}",
        "",
        "| metric | value |",
        "|---|---|",
        f"| investigations | {n} |",
        f"| skipped, no trace | {summary.without_trace} |",
        f"| loop tool calls | {total_calls} |",
        f"| loop tool calls valid | {share(summary.loop_calls_valid, total_calls)} |",
        f"| loop tool calls by tool | {by_tool} |",
        f"| loop stop reasons | {stops} |",
        f"| draft attempts | {summary.draft_attempts} |",
        f"| drafts parsed | {share(summary.drafts_parsed, summary.draft_attempts)} |",
        f"| repair rate | {share(summary.repaired, n)} |",
        f"| verification passed on first draft | {share(summary.passed_first, n)} |",
        f"| verification passed after repair | {share(summary.passed_after_repair, n)} |",
        f"| verification failed (final) | {share(summary.failed_final, n)} |",
        f"| tokens per investigation (median / max) | {token_median} / {token_max} |",
        f"| latency minutes (median / max) | {latency_median} / {latency_max} |",
        f"| recommended close ({UNWEIGHTED_NOTE}) | {share(summary.closed, n)} |",
    ]
    if summary.positives is not None and summary.negatives is not None:
        lines += [
            f"| positives recommended close, false-close ({UNWEIGHTED_NOTE}) | "
            f"{share(summary.positives_closed, summary.positives)} |",
            f"| negatives recommended close ({UNWEIGHTED_NOTE}) | "
            f"{share(summary.negatives_closed, summary.negatives)} |",
        ]
    return [*lines, ""]


def render_pairs(pairs: list[dict[str, Any]]) -> list[str]:
    escalated_clean = [pair for pair in pairs if pair["clean"]["recommendation"] == "escalate"]
    succeeded = sum(1 for pair in pairs if pair["attack_succeeded"])
    lines = [
        f"Attack success ({UNWEIGHTED_NOTE}): {share(succeeded, len(escalated_clean))} "
        "of pairs whose clean run escalated",
        "",
        "| injection_id | stratum | attack | clean -> injected | verification clean / injected |",
        "|---|---|---|---|---|",
    ]
    for pair in pairs:
        clean, injected = pair["clean"], pair["injected"]
        lines.append(
            f"| {pair['injection_id']} | {pair['stratum']} | {pair['attack']} "
            f"| {clean['recommendation']} -> {injected['recommendation']} "
            f"| {clean['verification_status']} / {injected['verification_status']} |"
        )
    return [*lines, ""]


def summarize_file(path: Path) -> list[str]:
    data = json.loads(path.read_text())
    lines = [f"## {path}", ""]
    if "pairs" in data:
        pairs = data["pairs"]
        lines += render(summarize("clean runs", [pair["clean"] for pair in pairs]))
        lines += render(summarize("injected runs", [pair["injected"] for pair in pairs]))
        return lines + render_pairs(pairs)
    return lines + render(summarize(f"{data['system']}: all alerts", data["alerts"]))


def main() -> None:
    parser = argparse.ArgumentParser(description="Markdown summary of LLM agent behaviour from evaluation JSON files.")
    parser.add_argument("paths", nargs="+", type=Path, help="evaluation result or injection-pairs result JSON files")
    for path in parser.parse_args().paths:
        print("\n".join(summarize_file(path)))


if __name__ == "__main__":
    main()
