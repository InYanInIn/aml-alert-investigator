from psycopg.conninfo import conninfo_to_dict, make_conninfo
from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="AML_", env_file=".env", extra="ignore", frozen=True)

    owner_dsn: SecretStr
    mcp_dsn: SecretStr
    app_dsn: SecretStr
    evaluator_dsn: SecretStr

    @property
    def owner_conninfo(self) -> str:
        return self.owner_dsn.get_secret_value()

    @property
    def mcp_conninfo(self) -> str:
        return self.mcp_dsn.get_secret_value()

    @property
    def app_conninfo(self) -> str:
        return self.app_dsn.get_secret_value()

    @property
    def evaluator_conninfo(self) -> str:
        return self.evaluator_dsn.get_secret_value()

    @property
    def role_dsns(self) -> tuple[str, ...]:
        return (self.mcp_conninfo, self.app_conninfo, self.evaluator_conninfo)

    @property
    def database(self) -> str:
        return str(conninfo_to_dict(self.owner_conninfo)["dbname"])

    def with_database(self, name: str) -> "Settings":
        def retargeted(dsn: SecretStr) -> SecretStr:
            return SecretStr(make_conninfo(dsn.get_secret_value(), dbname=name))

        return self.model_copy(
            update={
                "owner_dsn": retargeted(self.owner_dsn),
                "mcp_dsn": retargeted(self.mcp_dsn),
                "app_dsn": retargeted(self.app_dsn),
                "evaluator_dsn": retargeted(self.evaluator_dsn),
            }
        )


def load_settings() -> Settings:
    return Settings()
