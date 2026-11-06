from collections.abc import Mapping
from contextlib import AbstractAsyncContextManager

from aml_investigator.evaluation.baselines.b0 import open_always_escalate
from aml_investigator.evaluation.baselines.b1 import open_rule_score
from aml_investigator.evaluation.baselines.b3 import open_gradient_boosting
from aml_investigator.evaluation.baselines.runtime import RuntimeSettings
from aml_investigator.evaluation.systems import System
from aml_investigator.monitoring.thresholds import load_thresholds

SYSTEM_NAMES = ("b0", "b1", "b2", "b3", "b3_no_age", "scripted", "local", "azure")
TRAINED_SYSTEMS = frozenset({"b3", "b3_no_age"})
ACCOUNT_AGE = frozenset({"account_age_days"})


def open_system(
    name: str, settings: RuntimeSettings, *, train_labels: Mapping[int, bool] | None = None
) -> AbstractAsyncContextManager[System]:
    if name in TRAINED_SYSTEMS and train_labels is None:
        raise ValueError(f"system {name!r} trains on train-window labels; pass train_labels")
    match name:
        case "b0":
            return open_always_escalate()
        case "b1":
            return open_rule_score(settings.app_conninfo, load_thresholds(settings.database))
        case "b3":
            return open_gradient_boosting(settings.app_conninfo, train_labels or {})
        case "b3_no_age":
            return open_gradient_boosting(
                settings.app_conninfo, train_labels or {}, name=name, excluded_features=ACCOUNT_AGE
            )
        case "scripted":
            from aml_investigator.evaluation.baselines.agent import open_agent
            from aml_investigator.investigation.models import chat_model

            return open_agent(settings, chat_model("rule-score", settings.database), name)
        case "local":
            from aml_investigator.evaluation.baselines.agent import open_agent
            from aml_investigator.investigation.models import chat_model

            return open_agent(settings, chat_model("local", settings.database), name)
        case "azure":
            from aml_investigator.evaluation.baselines.agent import open_agent
            from aml_investigator.investigation.models import chat_model

            return open_agent(settings, chat_model("azure", settings.database, settings.azure_endpoint), name)
        case "b2":
            from aml_investigator.evaluation.baselines.b2 import open_single_prompt
            from aml_investigator.investigation.models import chat_model

            return open_single_prompt(settings, chat_model("azure", settings.database, settings.azure_endpoint))
    raise ValueError(f"unknown system {name!r}; valid systems: {', '.join(SYSTEM_NAMES)}")
