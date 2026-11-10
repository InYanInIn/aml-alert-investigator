from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass

from aml_investigator.core.db import QuerySource, open_read_only_pool
from aml_investigator.core.sql import load
from aml_investigator.evaluation.systems import EvalAlert, SystemDecision

SPENDS_OWN_MONEY_SQL = load(__package__, "spends_own_money")


@dataclass(frozen=True)
class OwnMoneyRule:
    pool: QuerySource
    name: str = "b4"

    async def decide(self, alert: EvalAlert) -> SystemDecision:
        async with self.pool.connection() as conn:
            cursor = await conn.execute(SPENDS_OWN_MONEY_SQL, {"alert_id": alert.alert_id})
            row = await cursor.fetchone()
        spends_own_money = row is not None and row["spends_own_money"]
        return SystemDecision(recommendation="close" if spends_own_money else "escalate")


@asynccontextmanager
async def open_own_money_rule(app_conninfo: str) -> AsyncIterator[OwnMoneyRule]:
    async with open_read_only_pool(app_conninfo) as pool:
        yield OwnMoneyRule(pool)
