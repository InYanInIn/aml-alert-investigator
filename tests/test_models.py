import pytest
from langchain_ollama import ChatOllama

from aml_investigator.investigation.models import chat_model
from aml_investigator.investigation.models.local import LOCAL_MODEL


def test_local_model_thinks_and_is_named_after_its_weights() -> None:
    model = chat_model("local", "aml_x")

    assert isinstance(model, ChatOllama)
    assert (model.model, model.get_name(), model.reasoning, model.temperature) == (LOCAL_MODEL, LOCAL_MODEL, True, None)


def test_unknown_model_lists_the_valid_names() -> None:
    with pytest.raises(ValueError, match="rule-score, local"):
        chat_model("gpt", "aml_x")
