from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass

from aml_investigator.evaluation.systems import EvalAlert, SystemDecision


@dataclass(frozen=True)
class AlwaysEscalate:
    name: str = "b0"

    async def decide(self, _alert: EvalAlert) -> SystemDecision:
        return SystemDecision(recommendation="escalate")


@asynccontextmanager
async def open_always_escalate() -> AsyncIterator[AlwaysEscalate]:
    yield AlwaysEscalate()
