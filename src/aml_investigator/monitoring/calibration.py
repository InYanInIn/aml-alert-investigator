import json
import math
import warnings
from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any

import psycopg

from aml_investigator.core.sql import load
from aml_investigator.data.splits import DayRange, Split, day_splits
from aml_investigator.monitoring.alerts import FIRST_DAYS_CTES, first_days_parameters
from aml_investigator.monitoring.rules import rules

CURVE_QUANTILES = (0.5, 0.75, 0.9, 0.95, 0.98, 0.99, 0.995, 0.999)

DATA_DAYS_SQL = "SELECT min(ts)::date, max(ts)::date FROM core.transactions"

LOWEST_THRESHOLDS_SQL = load(__package__, "lowest_thresholds")

SCORE_QUANTILES_SQL = """
SELECT rule_id, percentile_disc(%(quantiles)s::float8[]) WITHIN GROUP (ORDER BY score)
FROM core.rule_candidates
WHERE rule_id = ANY (%(rule_ids)s::text[]) AND alert_date BETWEEN %(first_day)s::date AND %(last_day)s::date
GROUP BY rule_id
"""

LAUNDERING_SIDES_CTE = load(__package__, "laundering_sides_cte")

RULE_CURVE_SQL = f"WITH {FIRST_DAYS_CTES},\n{LAUNDERING_SIDES_CTE},\n{load(__package__, 'rule_curve')}"

FEASIBILITY_SQL = f"WITH {FIRST_DAYS_CTES},\n{LAUNDERING_SIDES_CTE}\n{load(__package__, 'feasibility')}"


@dataclass(frozen=True)
class CurvePoint:
    rule_id: str
    quantile: float
    threshold: float
    days: int
    alerts: int
    positive_alerts: int
    attempts_touched: int
    attempts_in_window: int

    @property
    def alerts_per_day(self) -> float:
        return self.alerts / self.days

    @property
    def positive_share(self) -> float:
        return self.positive_alerts / self.alerts if self.alerts else 0.0


@dataclass(frozen=True)
class Feasibility:
    days: int
    alerts: int
    positive_alerts: int

    @property
    def alerts_per_day(self) -> float:
        return self.alerts / self.days

    @property
    def positive_per_day(self) -> float:
        return self.positive_alerts / self.days

    @property
    def projected_test_positives(self) -> float:
        return self.positive_per_day * self.days


def train_split(conn: psycopg.Connection[Any]) -> dict[Split, DayRange]:
    [(first_day, last_day)] = conn.execute(DATA_DAYS_SQL).fetchall()
    if first_day is None:
        raise ValueError("core.transactions is empty; nothing to split")
    return day_splits(first_day, last_day)


def day_count(days: DayRange) -> int:
    return (days.last - days.first).days + 1


def choose_thresholds(
    conn: psycopg.Connection[Any], train: DayRange, *, budget_per_day: float, suppression_days: int
) -> dict[str, float]:
    rule_ids = [rule.RULE_ID for rule in rules()]
    share_per_day = budget_per_day / len(rule_ids)
    parameters = {
        "rule_ids": rule_ids,
        "first_day": train.first,
        "last_day": train.last,
        "suppression_days": suppression_days,
        "max_alerts": share_per_day * day_count(train),
    }
    lowest = {rule_id: (top, threshold) for rule_id, top, threshold in conn.execute(LOWEST_THRESHOLDS_SQL, parameters)}
    missing = [rule_id for rule_id in rule_ids if rule_id not in lowest]
    if missing:
        raise ValueError(f"no train-window candidates for rules: {', '.join(missing)}")
    chosen = {}
    for rule_id in rule_ids:
        top, threshold = lowest[rule_id]
        if threshold is None:
            warnings.warn(
                f"{rule_id}: even its highest train score ({top}) exceeds {share_per_day:.2f} alerts/day; switched off",
                stacklevel=2,
            )
            chosen[rule_id] = float_above(top)
        else:
            chosen[rule_id] = float_at_or_below(threshold)
    return chosen


def float_at_or_below(level: Decimal) -> float:
    value = float(level)
    return value if Decimal(value) <= level else math.nextafter(value, -math.inf)


def float_above(level: Decimal) -> float:
    value = float(level)
    return value if Decimal(value) > level else math.nextafter(value, math.inf)


def rule_curves(conn: psycopg.Connection[Any], train: DayRange, *, suppression_days: int) -> list[CurvePoint]:
    window = {"rule_ids": [rule.RULE_ID for rule in rules()], "first_day": train.first, "last_day": train.last}
    quantile_scores = dict(conn.execute(SCORE_QUANTILES_SQL, {**window, "quantiles": list(CURVE_QUANTILES)}).fetchall())
    points = []
    for index, quantile in enumerate(CURVE_QUANTILES):
        thresholds = {rule_id: float_at_or_below(scores[index]) for rule_id, scores in quantile_scores.items()}
        parameters = first_days_parameters(thresholds, train, suppression_days=suppression_days)
        for rule_id, threshold, alerts, positives, touched, attempts in conn.execute(RULE_CURVE_SQL, parameters):
            points.append(
                CurvePoint(rule_id, quantile, float(threshold), day_count(train), alerts, positives, touched, attempts)
            )
    return sorted(points, key=lambda point: (point.rule_id, point.quantile))


def feasibility(
    conn: psycopg.Connection[Any], val: DayRange, thresholds: Mapping[str, float], *, suppression_days: int
) -> Feasibility:
    parameters = first_days_parameters(thresholds, val, suppression_days=suppression_days)
    [(alerts, positive_alerts)] = conn.execute(FEASIBILITY_SQL, parameters).fetchall()
    return Feasibility(day_count(val), alerts, positive_alerts)


def write_thresholds(
    path: Path, thresholds: Mapping[str, float], *, budget_per_day: float, suppression_days: int, train: DayRange
) -> None:
    config = {
        "budget_per_day": budget_per_day,
        "suppression_days": suppression_days,
        "calibrated_on": {"first": train.first.isoformat(), "last": train.last.isoformat()},
        "thresholds": dict(thresholds),
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(config, indent=2) + "\n")
