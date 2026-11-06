from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from langchain_core.language_models import BaseChatModel

MODEL_NAMES = ("rule-score", "local")


def chat_model(name: str, database: str) -> "BaseChatModel":
    match name:
        case "rule-score":
            from aml_investigator.investigation.models.rule_score import rule_score_model

            return rule_score_model(database)
        case "local":
            from aml_investigator.investigation.models.local import local_model

            return local_model()
    raise ValueError(f"unknown model {name!r}; valid models: {', '.join(MODEL_NAMES)}")
