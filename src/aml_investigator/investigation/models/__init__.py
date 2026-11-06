from typing import TYPE_CHECKING

from aml_investigator.core.settings import MissingSettingError

if TYPE_CHECKING:
    from langchain_core.language_models import BaseChatModel

MODEL_NAMES = ("rule-score", "local", "azure")


def chat_model(name: str, database: str, azure_endpoint: str | None = None) -> "BaseChatModel":
    match name:
        case "rule-score":
            from aml_investigator.investigation.models.rule_score import rule_score_model

            return rule_score_model(database)
        case "local":
            from aml_investigator.investigation.models.local import local_model

            return local_model()
        case "azure":
            from aml_investigator.investigation.models.azure import azure_model

            if not azure_endpoint:
                raise MissingSettingError("AML_AZURE_OPENAI_ENDPOINT")
            return azure_model(azure_endpoint)
    raise ValueError(f"unknown model {name!r}; valid models: {', '.join(MODEL_NAMES)}")
