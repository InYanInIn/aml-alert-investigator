import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    owner_dsn: str
    mcp_dsn: str
    app_dsn: str
    evaluator_dsn: str

    @property
    def role_dsns(self) -> tuple[str, ...]:
        return (self.mcp_dsn, self.app_dsn, self.evaluator_dsn)


def load_settings() -> Settings:
    names = ("AML_OWNER_DSN", "AML_MCP_DSN", "AML_APP_DSN", "AML_EVALUATOR_DSN")
    missing = [name for name in names if not os.environ.get(name)]
    if missing:
        raise RuntimeError(f"missing environment variables: {', '.join(missing)} (see .env.example)")
    return Settings(*(os.environ[name] for name in names))
