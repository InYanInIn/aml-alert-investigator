from dataclasses import dataclass, field

from aml_investigator.core.settings import Settings
from aml_investigator.mcp_server.schemas import ReferenceVariant


@dataclass(frozen=True)
class RuntimeSettings:
    app_conninfo: str = field(repr=False)
    mcp_conninfo: str = field(repr=False)
    database: str
    reference_variant: ReferenceVariant = "clean"
    azure_endpoint: str | None = None

    @classmethod
    def of(cls, settings: Settings, reference_variant: ReferenceVariant = "clean") -> "RuntimeSettings":
        return cls(
            settings.app_conninfo,
            settings.mcp_conninfo,
            settings.database,
            reference_variant,
            settings.azure_openai_endpoint,
        )
