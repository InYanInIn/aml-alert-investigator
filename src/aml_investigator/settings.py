import os
from dataclasses import dataclass

from psycopg.conninfo import conninfo_to_dict, make_conninfo


@dataclass(frozen=True)
class Settings:
    owner_dsn: str
    mcp_dsn: str
    app_dsn: str
    evaluator_dsn: str

    @property
    def role_dsns(self) -> tuple[str, ...]:
        return (self.mcp_dsn, self.app_dsn, self.evaluator_dsn)

    @property
    def database(self) -> str:
        return str(conninfo_to_dict(self.owner_dsn)["dbname"])

    def with_database(self, name: str) -> "Settings":
        return Settings(
            owner_dsn=make_conninfo(self.owner_dsn, dbname=name),
            mcp_dsn=make_conninfo(self.mcp_dsn, dbname=name),
            app_dsn=make_conninfo(self.app_dsn, dbname=name),
            evaluator_dsn=make_conninfo(self.evaluator_dsn, dbname=name),
        )


def load_settings() -> Settings:
    names = ("AML_OWNER_DSN", "AML_MCP_DSN", "AML_APP_DSN", "AML_EVALUATOR_DSN")
    missing = [name for name in names if not os.environ.get(name)]
    if missing:
        raise RuntimeError(f"missing environment variables: {', '.join(missing)} (see .env.example)")
    return Settings(*(os.environ[name] for name in names))
