import json
from typing import Any

import httpx
import openai
import pydantic
import pytest
from langchain_core.messages import AIMessage
from langchain_core.messages.ai import UsageMetadata
from langchain_openai import AzureChatOpenAI

from aml_investigator.core.settings import MissingSettingError
from aml_investigator.evaluation.baselines import open_system
from aml_investigator.evaluation.baselines.runtime import RuntimeSettings
from aml_investigator.investigation.agent.usage import TokenUsage
from aml_investigator.investigation.decision import Decision
from aml_investigator.investigation.models import azure as azure_module
from aml_investigator.investigation.models import chat_model

ENDPOINT = "https://example-resource.openai.azure.com"
SCOPE = "https://cognitiveservices.azure.com/.default"


class StubCredential:
    pass


@pytest.fixture
def requested_scopes(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    scopes: list[str] = []

    def token_provider(credential: object, scope: str) -> Any:
        assert isinstance(credential, StubCredential)
        scopes.append(scope)
        return lambda: "entra-token"

    monkeypatch.setattr(azure_module, "DefaultAzureCredential", StubCredential)
    monkeypatch.setattr(azure_module, "get_bearer_token_provider", token_provider)
    return scopes


def test_azure_model_uses_the_deployment_low_reasoning_and_entra_id_only(
    requested_scopes: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "ambient-key")
    monkeypatch.setenv("AZURE_OPENAI_API_KEY", "ambient-key")

    model = chat_model("azure", "aml_x", ENDPOINT)

    assert isinstance(model, AzureChatOpenAI)
    assert (model.deployment_name, model.model_name, model.get_name()) == ("gpt-5-mini", "gpt-5-mini", "gpt-5-mini")
    assert (model.reasoning_effort, model.openai_api_version) == (
        azure_module.REASONING_EFFORT,
        azure_module.API_VERSION,
    )
    assert model.azure_endpoint == ENDPOINT
    assert requested_scopes == [SCOPE]
    assert model.openai_api_key is None
    assert model.azure_ad_token_provider is not None
    assert model.azure_ad_token_provider() == "entra-token"


@pytest.mark.parametrize("endpoint", [None, ""])
def test_azure_model_without_an_endpoint_names_the_missing_setting(endpoint: str | None) -> None:
    with pytest.raises(MissingSettingError, match="AML_AZURE_OPENAI_ENDPOINT"):
        chat_model("azure", "aml_x", endpoint)


def test_azure_system_without_an_endpoint_fails_when_opened() -> None:
    with pytest.raises(MissingSettingError, match="AML_AZURE_OPENAI_ENDPOINT"):
        open_system("azure", RuntimeSettings("app", "mcp", "aml_x"))


def completion(message: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": "chatcmpl-1",
        "object": "chat.completion",
        "created": 1,
        "model": "gpt-5-mini",
        "choices": [{"index": 0, "finish_reason": "stop", "message": {"role": "assistant", **message}}],
        "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
    }


def tool_call_message(arguments: str) -> dict[str, Any]:
    call = {"id": "call_1", "type": "function", "function": {"name": "Decision", "arguments": arguments}}
    return {"content": None, "tool_calls": [call]}


def offline_azure_model(requested: list[httpx.Request], message: dict[str, Any]) -> AzureChatOpenAI:
    def handler(request: httpx.Request) -> httpx.Response:
        requested.append(request)
        return httpx.Response(200, json=completion(message))

    model = azure_module.azure_model(ENDPOINT)
    model.root_client = openai.AzureOpenAI(  # type: ignore[call-overload]
        azure_endpoint=ENDPOINT,
        api_version=azure_module.API_VERSION,
        api_key="offline-placeholder",
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    model.client = model.root_client.chat.completions
    return model


@pytest.mark.usefixtures("requested_scopes")
@pytest.mark.parametrize(
    ("message", "parsing_error"),
    [
        ({"content": "I cannot produce JSON today."}, False),
        (tool_call_message('{"recommendation": "escalate"}'), True),
        (tool_call_message("{not json"), False),
    ],
    ids=["no tool call", "arguments fail the schema", "arguments are not json"],
)
def test_malformed_completion_is_returned_as_a_result_not_raised(
    message: dict[str, Any], *, parsing_error: bool
) -> None:
    requested: list[httpx.Request] = []
    structured = offline_azure_model(requested, message).with_structured_output(Decision, include_raw=True)

    output = structured.invoke("decide")

    assert output["parsed"] is None
    assert (output["parsing_error"] is not None) is parsing_error
    assert isinstance(output["raw"], AIMessage)
    body = json.loads(requested[0].content)
    assert (body["model"], body["reasoning_effort"], body["tool_choice"]["function"]["name"]) == (
        "gpt-5-mini",
        azure_module.REASONING_EFFORT,
        "Decision",
    )
    assert f"api-version={azure_module.API_VERSION}" in str(requested[0].url)


@pytest.mark.usefixtures("requested_scopes")
def test_json_schema_method_raises_inside_the_sdk_on_schema_invalid_content() -> None:
    model = offline_azure_model([], {"content": '{"recommendation": "escalate"}'})

    with pytest.raises(pydantic.ValidationError):
        model.with_structured_output(Decision, method="json_schema", include_raw=True).invoke("decide")


def usage(input_tokens: int, output_tokens: int, cached: int | None) -> AIMessage:
    metadata: UsageMetadata = {
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "total_tokens": input_tokens + output_tokens,
    }
    if cached is not None:
        metadata["input_token_details"] = {"cache_read": cached}
    return AIMessage("", usage_metadata=metadata)


def test_usage_is_read_from_usage_metadata_and_priced_for_gpt_5_mini() -> None:
    total = TokenUsage.of(usage(10_000, 2_000, cached=4_000)) + TokenUsage.of(usage(1_000, 500, cached=None))

    assert total == TokenUsage(input_tokens=11_000, cached_input_tokens=4_000, output_tokens=2_500)
    assert total.cost_usd("gpt-5-mini") == pytest.approx(7_000 * 0.25e-6 + 4_000 * 0.025e-6 + 2_500 * 2.00e-6)


def test_models_without_a_price_have_no_cost() -> None:
    total = TokenUsage.of(usage(100, 10, cached=0))

    assert (total.cost_usd("rule-score"), total.cost_usd("qwen3.6:35b-a3b-q8_0")) == (None, None)
    assert TokenUsage.of(AIMessage("no usage")) == TokenUsage()
