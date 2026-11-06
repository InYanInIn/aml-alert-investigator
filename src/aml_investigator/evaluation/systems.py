from dataclasses import dataclass
from datetime import date
from typing import Any, Literal, Protocol

Recommendation = Literal["close", "escalate"]
CONCURRENCY = 8


@dataclass(frozen=True)
class EvalAlert:
    alert_id: int
    account_id: str
    alert_date: date


@dataclass(frozen=True)
class SystemDecision:
    recommendation: Recommendation | None
    risk_score: float | None = None
    typologies: tuple[str, ...] = ()
    verification_status: str | None = None
    cost_usd: float | None = None
    latency_ms: float | None = None
    trace: dict[str, Any] | None = None


class System(Protocol):
    @property
    def name(self) -> str: ...

    async def decide(self, alert: EvalAlert) -> SystemDecision: ...
