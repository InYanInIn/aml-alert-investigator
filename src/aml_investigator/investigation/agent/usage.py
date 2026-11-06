from langchain_core.messages import AIMessage
from pydantic import BaseModel

TOKENS_PER_PRICE_UNIT = 1_000_000
PRICES_USD_PER_MILLION_TOKENS: dict[str, tuple[float, float, float]] = {"gpt-5-mini": (0.25, 0.025, 2.00)}


class TokenUsage(BaseModel, frozen=True):
    input_tokens: int = 0
    cached_input_tokens: int = 0
    output_tokens: int = 0

    @classmethod
    def of(cls, message: AIMessage) -> "TokenUsage":
        usage = message.usage_metadata
        if not usage:
            return cls()
        cached = (usage.get("input_token_details") or {}).get("cache_read", 0)
        return cls(input_tokens=usage["input_tokens"], cached_input_tokens=cached, output_tokens=usage["output_tokens"])

    def __add__(self, other: "TokenUsage") -> "TokenUsage":
        return TokenUsage(
            input_tokens=self.input_tokens + other.input_tokens,
            cached_input_tokens=self.cached_input_tokens + other.cached_input_tokens,
            output_tokens=self.output_tokens + other.output_tokens,
        )

    def cost_usd(self, model: str) -> float | None:
        if model not in PRICES_USD_PER_MILLION_TOKENS:
            return None
        fresh, cached, output = PRICES_USD_PER_MILLION_TOKENS[model]
        cost = (
            (self.input_tokens - self.cached_input_tokens) * fresh
            + self.cached_input_tokens * cached
            + self.output_tokens * output
        )
        return cost / TOKENS_PER_PRICE_UNIT
