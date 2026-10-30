from dataclasses import dataclass, field

from aml_investigator.core.settings import Settings


@dataclass(frozen=True)
class RuntimeSettings:
    app_conninfo: str = field(repr=False)
    mcp_conninfo: str = field(repr=False)
    database: str

    @classmethod
    def of(cls, settings: Settings) -> "RuntimeSettings":
        return cls(settings.app_conninfo, settings.mcp_conninfo, settings.database)
