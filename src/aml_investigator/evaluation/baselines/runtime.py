from dataclasses import dataclass, field
from pathlib import Path

from aml_investigator.core.settings import Settings
from aml_investigator.mcp_server.schemas import ReferenceVariant


@dataclass(frozen=True)
class RuntimeSettings:
    app_conninfo: str = field(repr=False)
    mcp_conninfo: str = field(repr=False)
    database: str
    reference_variant: ReferenceVariant = "clean"
    azure_endpoint: str | None = None
    recordings: Path | None = None
    customer_profile: bool = False

    @classmethod
    def of(
        cls, settings: Settings, reference_variant: ReferenceVariant = "clean", recordings: Path | None = None
    ) -> "RuntimeSettings":
        return cls(
            settings.app_conninfo,
            settings.mcp_conninfo,
            settings.database,
            reference_variant,
            settings.azure_openai_endpoint,
            recordings,
        )
