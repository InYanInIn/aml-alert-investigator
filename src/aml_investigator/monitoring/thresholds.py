import json
import math
from collections.abc import Iterable, Mapping
from pathlib import Path

THRESHOLDS_DIR = Path("config/thresholds")


def load_thresholds(database: str) -> dict[str, float]:
    config = json.loads((THRESHOLDS_DIR / f"{database}.json").read_text())
    return {rule_id: float(threshold) for rule_id, threshold in config["thresholds"].items()}


def rule_score(hits: Iterable[tuple[str, float]], thresholds: Mapping[str, float]) -> float:
    return math.fsum(score / thresholds[rule_id] for rule_id, score in hits)
