import json
import math
from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path

from aml_investigator.core.db import Pool, open_read_only_pool
from aml_investigator.core.sql import load
from aml_investigator.evaluation.systems import EvalAlert, SystemDecision

THRESHOLDS_DIR = Path("config/thresholds")
RULE_HIT_SCORES_SQL = load(__package__, "rule_hit_scores")


def load_thresholds(database: str) -> dict[str, float]:
    config = json.loads((THRESHOLDS_DIR / f"{database}.json").read_text())
    return {rule_id: float(threshold) for rule_id, threshold in config["thresholds"].items()}


@dataclass(frozen=True)
class RuleScore:
    pool: Pool
    thresholds: Mapping[str, float]
    name: str = "b1"

    async def decide(self, alert: EvalAlert) -> SystemDecision:
        async with self.pool.connection() as conn:
            cursor = await conn.execute(RULE_HIT_SCORES_SQL, {"alert_id": alert.alert_id})
            hits = await cursor.fetchall()
        risk_score = math.fsum(float(hit["score"]) / self.thresholds[hit["rule_id"]] for hit in hits)
        return SystemDecision(recommendation=None, risk_score=risk_score)


@asynccontextmanager
async def open_rule_score(app_conninfo: str, thresholds: Mapping[str, float]) -> AsyncIterator[RuleScore]:
    async with open_read_only_pool(app_conninfo) as pool:
        yield RuleScore(pool, thresholds)
