import math
import random
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass

from aml_investigator.evaluation.systems import Recommendation

BOOTSTRAP_RESAMPLES = 2000
BOOTSTRAP_SEED = 20261030
INTERVAL_TAILS = (0.025, 0.975)
MAX_FALSE_CLOSE_RATE = 0.05


@dataclass(frozen=True)
class Outcome:
    escalate_truth: bool
    truth_typologies: tuple[str, ...]
    recommendation: Recommendation
    typologies: tuple[str, ...] = ()
    cost_usd: float | None = None
    latency_ms: float | None = None

    @property
    def closed(self) -> bool:
        return self.recommendation == "close"

    @property
    def typology_hit(self) -> bool:
        return bool(set(self.typologies) & set(self.truth_typologies))


@dataclass(frozen=True)
class Estimate:
    value: float | None
    low: float | None
    high: float | None


@dataclass(frozen=True)
class MetricReport:
    weighted: dict[str, Estimate]
    unweighted: dict[str, Estimate]


@dataclass(frozen=True)
class ScoredAlert:
    risk_score: float
    escalate_truth: bool


@dataclass(frozen=True)
class OperatingPoint:
    threshold: float
    close_rate: float
    false_close_rate: float


Weighted = Sequence[tuple[Outcome, float]]


def stratum_weights(population: Mapping[bool, int], truths: Sequence[bool]) -> dict[bool, float]:
    population_total = sum(population.values())
    weights: dict[bool, float] = {}
    for stratum in (True, False):
        drawn = truths.count(stratum)
        if population[stratum] and not drawn:
            raise ValueError(f"the sample has no alerts with escalate_truth={stratum}; it cannot be reweighted")
        weights[stratum] = (population[stratum] / population_total) / (drawn / len(truths)) if drawn else 0.0
    return weights


def measure(outcomes: Sequence[Outcome], weights: Sequence[float]) -> dict[str, float | None]:
    rows = list(zip(outcomes, weights, strict=True))
    positives = [(o, w) for o, w in rows if o.escalate_truth]
    escalated = [(o, w) for o, w in rows if not o.closed]
    pattern = [(o, w) for o, w in positives if o.truth_typologies]
    other = [(o, w) for o, w in positives if not o.truth_typologies]
    emits_typologies = any(o.typologies for o in outcomes)
    latencies = [(o.latency_ms, w) for o, w in rows if o.latency_ms is not None]
    costs = [(o.cost_usd, w) for o, w in rows if o.cost_usd is not None]
    return {
        "false_close_rate": share(positives, lambda o: o.closed),
        "escalation_precision": share(escalated, lambda o: o.escalate_truth),
        "close_rate": share(rows, lambda o: o.closed),
        "recall_pattern": share(pattern, lambda o: not o.closed),
        "recall_other": share(other, lambda o: not o.closed),
        "typology_hit_rate": share(pattern, lambda o: o.typology_hit) if emits_typologies else None,
        "mean_latency_ms": mean(latencies),
        "p50_latency_ms": quantile(latencies, 0.5),
        "p95_latency_ms": quantile(latencies, 0.95),
        "mean_cost_usd": mean(costs),
    }


def share(rows: Weighted, counted: Callable[[Outcome], bool]) -> float | None:
    total = sum(w for _, w in rows)
    return sum(w for o, w in rows if counted(o)) / total if total else None


def mean(values: Sequence[tuple[float, float]]) -> float | None:
    total = sum(w for _, w in values)
    return sum(v * w for v, w in values) / total if total else None


def quantile(values: Sequence[tuple[float, float]], q: float) -> float | None:
    ordered = sorted(values)
    target = q * sum(w for _, w in ordered)
    cumulative = 0.0
    for value, weight in ordered:
        cumulative += weight
        if cumulative >= target:
            return value
    return ordered[-1][0] if ordered else None


def report(
    outcomes: Sequence[Outcome],
    population: Mapping[bool, int],
    *,
    resamples: int = BOOTSTRAP_RESAMPLES,
    seed: int = BOOTSTRAP_SEED,
) -> MetricReport:
    weights = stratum_weights(population, [o.escalate_truth for o in outcomes])
    rng = random.Random(seed)  # noqa: S311
    strata = [[o for o in outcomes if o.escalate_truth is stratum] for stratum in (True, False)]
    draws = [[o for stratum in strata for o in rng.choices(stratum, k=len(stratum))] for _ in range(resamples)]
    return MetricReport(
        weighted=estimates(outcomes, draws, lambda o: weights[o.escalate_truth]),
        unweighted=estimates(outcomes, draws, lambda _: 1.0),
    )


def estimates(
    outcomes: Sequence[Outcome], draws: Sequence[Sequence[Outcome]], weight_of: Callable[[Outcome], float]
) -> dict[str, Estimate]:
    point = measure(outcomes, [weight_of(o) for o in outcomes])
    resampled = [measure(draw, [weight_of(o) for o in draw]) for draw in draws]
    return {name: Estimate(value, *interval(r[name] for r in resampled)) for name, value in point.items()}


def interval(values: Iterable[float | None]) -> tuple[float | None, float | None]:
    present = sorted(v for v in values if v is not None)
    if not present:
        return None, None
    low, high = (present[round(tail * (len(present) - 1))] for tail in INTERVAL_TAILS)
    return low, high


def choose_operating_point(
    scored: Sequence[ScoredAlert],
    weights: Mapping[bool, float],
    max_false_close_rate: float = MAX_FALSE_CLOSE_RATE,
) -> OperatingPoint:
    if not scored:
        raise ValueError("an operating point needs at least one scored alert")
    scores = sorted({alert.risk_score for alert in scored})
    candidates = [*scores, math.nextafter(scores[-1], math.inf)]
    points = [operating_point_at(threshold, scored, weights) for threshold in candidates]
    feasible = [point for point in points if point.false_close_rate <= max_false_close_rate]
    return max(feasible, key=lambda point: point.close_rate)


def operating_point_at(
    threshold: float, scored: Sequence[ScoredAlert], weights: Mapping[bool, float]
) -> OperatingPoint:
    rows = [(alert.risk_score < threshold, alert.escalate_truth, weights[alert.escalate_truth]) for alert in scored]
    total = sum(w for *_, w in rows)
    positives = sum(w for _, positive, w in rows if positive)
    closed = sum(w for close, _, w in rows if close)
    closed_positives = sum(w for close, positive, w in rows if close and positive)
    return OperatingPoint(
        threshold=threshold,
        close_rate=closed / total,
        false_close_rate=closed_positives / positives if positives else 0.0,
    )


def decide_at(point: OperatingPoint, risk_score: float) -> Recommendation:
    return "close" if risk_score < point.threshold else "escalate"
