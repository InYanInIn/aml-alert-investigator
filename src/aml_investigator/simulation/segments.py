import json
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import psycopg

from aml_investigator.core.sql import load
from aml_investigator.data.splits import DayRange
from aml_investigator.evaluation.results import THRESHOLDS_DIR
from aml_investigator.monitoring.calibration import choose_thresholds
from aml_investigator.simulation.world import MONITORED_BANK_ID

SEGMENTS = {
    "individual": ("Individual",),
    "small_business": ("Sole Proprietorship", "Partnership"),
    "corporation": ("Corporation",),
}

MONITORED_ACCOUNTS_SQL = load(__package__, "monitored_accounts_by_type")
DELETE_OUTSIDE_SEGMENT_SQL = load(__package__, "delete_candidates_outside_segment")
DELETE_BELOW_THRESHOLD_SQL = load(__package__, "delete_candidates_below_segment_threshold")


@dataclass(frozen=True)
class SegmentThresholds:
    suppression_days: int
    thresholds: dict[str, dict[str, float]]


def segments_path(database: str) -> Path:
    return THRESHOLDS_DIR / f"{database}.segments.json"


def budget_shares(conn: psycopg.Connection[Any], budget_per_day: float) -> dict[str, float]:
    counts = dict(conn.execute(MONITORED_ACCOUNTS_SQL, {"monitored_bank_id": MONITORED_BANK_ID}).fetchall())
    per_segment = {segment: sum(counts.get(kind, 0) for kind in kinds) for segment, kinds in SEGMENTS.items()}
    total = sum(per_segment.values())
    return {segment: budget_per_day * count / total for segment, count in per_segment.items()}


def calibrate_segments(
    conn: psycopg.Connection[Any], train: DayRange, *, budget_per_day: float, suppression_days: int
) -> dict[str, dict[str, float]]:
    chosen: dict[str, dict[str, float]] = {}
    for segment, share in budget_shares(conn, budget_per_day).items():
        if share == 0:
            chosen[segment] = {}
            continue
        with conn.transaction(force_rollback=True):
            conn.execute(DELETE_OUTSIDE_SEGMENT_SQL, {"entity_types": list(SEGMENTS[segment])})
            chosen[segment] = choose_thresholds(conn, train, budget_per_day=share, suppression_days=suppression_days)
    return chosen


def apply_segment_thresholds(conn: psycopg.Connection[Any], thresholds: Mapping[str, Mapping[str, float]]) -> None:
    rows = [
        (kind, rule_id, float(limit))
        for segment, kinds in SEGMENTS.items()
        for kind in kinds
        for rule_id, limit in thresholds.get(segment, {}).items()
    ]
    conn.execute(
        DELETE_BELOW_THRESHOLD_SQL,
        {
            "entity_types": [row[0] for row in rows],
            "rule_ids": [row[1] for row in rows],
            "limits": [row[2] for row in rows],
        },
    )


def write_segments(
    database: str,
    thresholds: Mapping[str, Mapping[str, float]],
    *,
    budget_per_day: float,
    suppression_days: int,
    train: DayRange,
) -> Path:
    path = segments_path(database)
    path.parent.mkdir(parents=True, exist_ok=True)
    config = {
        "budget_per_day": budget_per_day,
        "suppression_days": suppression_days,
        "calibrated_on": {"first": train.first.isoformat(), "last": train.last.isoformat()},
        "segments": {segment: dict(rule_limits) for segment, rule_limits in thresholds.items()},
    }
    path.write_text(json.dumps(config, indent=2) + "\n")
    return path


def load_segments(database: str) -> SegmentThresholds:
    path = segments_path(database)
    if not path.exists():
        raise FileNotFoundError(f"no segment thresholds for {database}: {path} is missing; run `aml sim calibrate`")
    file = json.loads(path.read_text())
    return SegmentThresholds(file["suppression_days"], file["segments"])
