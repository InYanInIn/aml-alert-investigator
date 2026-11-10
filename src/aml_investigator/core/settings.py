from decimal import Decimal

from psycopg.conninfo import conninfo_to_dict, make_conninfo
from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

from aml_investigator.core.errors import DomainError


class MissingSettingError(DomainError):
    def __init__(self, name: str) -> None:
        super().__init__(f"{name} is not set for this process")


def required(value: SecretStr | None, name: str) -> str:
    if value is None:
        raise MissingSettingError(name)
    return value.get_secret_value()


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="AML_", env_file=".env", extra="ignore", frozen=True)

    owner_dsn: SecretStr | None = None
    mcp_dsn: SecretStr | None = None
    app_dsn: SecretStr | None = None
    evaluator_dsn: SecretStr | None = None
    langfuse_public_key: str | None = None
    langfuse_secret_key: SecretStr | None = None
    langfuse_host: str | None = None
    azure_openai_endpoint: str | None = None
    ui_spend_limit_usd: Decimal = Field(default=Decimal("39.00"), ge=0, allow_inf_nan=False)

    @property
    def owner_conninfo(self) -> str:
        return required(self.owner_dsn, "AML_OWNER_DSN")

    @property
    def mcp_conninfo(self) -> str:
        return required(self.mcp_dsn, "AML_MCP_DSN")

    @property
    def app_conninfo(self) -> str:
        return required(self.app_dsn, "AML_APP_DSN")

    @property
    def evaluator_conninfo(self) -> str:
        return required(self.evaluator_dsn, "AML_EVALUATOR_DSN")

    @property
    def azure_endpoint(self) -> str:
        if not self.azure_openai_endpoint:
            raise MissingSettingError("AML_AZURE_OPENAI_ENDPOINT")
        return self.azure_openai_endpoint

    @property
    def role_dsns(self) -> tuple[str, ...]:
        return (self.mcp_conninfo, self.app_conninfo, self.evaluator_conninfo)

    @property
    def database(self) -> str:
        dsns = (self.owner_dsn, self.app_dsn, self.mcp_dsn, self.evaluator_dsn)
        dsn = required(next((dsn for dsn in dsns if dsn is not None), None), "AML_APP_DSN or another role DSN")
        return str(conninfo_to_dict(dsn)["dbname"])

    def with_database(self, name: str) -> "Settings":
        def retargeted(dsn: SecretStr | None) -> SecretStr | None:
            return None if dsn is None else SecretStr(make_conninfo(dsn.get_secret_value(), dbname=name))

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
