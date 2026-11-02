from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass

from aml_investigator.core.db import Pool, open_read_only_pool
from aml_investigator.core.sql import load
from aml_investigator.evaluation.systems import EvalAlert, SystemDecision
from aml_investigator.monitoring.thresholds import rule_score

RULE_HIT_SCORES_SQL = load(__package__, "rule_hit_scores")


@dataclass(frozen=True)
class RuleScore:
    pool: Pool
    thresholds: Mapping[str, float]
    name: str = "b1"

    async def decide(self, alert: EvalAlert) -> SystemDecision:
        async with self.pool.connection() as conn:
            cursor = await conn.execute(RULE_HIT_SCORES_SQL, {"alert_id": alert.alert_id})
            hits = await cursor.fetchall()
        score = rule_score(((hit["rule_id"], float(hit["score"])) for hit in hits), self.thresholds)
        return SystemDecision(recommendation=None, risk_score=score)


@asynccontextmanager
async def open_rule_score(app_conninfo: str, thresholds: Mapping[str, float]) -> AsyncIterator[RuleScore]:
    async with open_read_only_pool(app_conninfo) as pool:
        yield RuleScore(pool, thresholds)
