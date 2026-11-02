from typing import Any

import pytest
from langchain_core.callbacks import BaseCallbackHandler
from pydantic import SecretStr

from aml_investigator.core import tracing
from aml_investigator.core.settings import Settings

DSN = SecretStr("dbname=none")


@pytest.fixture(autouse=True)
def no_langfuse_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("AML_LANGFUSE_PUBLIC_KEY", "AML_LANGFUSE_SECRET_KEY", "AML_LANGFUSE_HOST"):
        monkeypatch.delenv(name, raising=False)


def settings(**langfuse: Any) -> Settings:
    return Settings(_env_file=None, owner_dsn=DSN, mcp_dsn=DSN, app_dsn=DSN, evaluator_dsn=DSN, **langfuse)


@pytest.mark.parametrize(
    "langfuse",
    [
        {},
        {"langfuse_public_key": "pk-lf-test", "langfuse_host": "http://127.0.0.1:9"},
        {"langfuse_secret_key": SecretStr("sk-lf-test"), "langfuse_host": "http://127.0.0.1:9"},
    ],
)
def test_without_keys_no_langfuse_client_is_created(langfuse: dict[str, Any], monkeypatch: pytest.MonkeyPatch) -> None:
    def forbidden(*_: Any, **__: Any) -> None:
        raise AssertionError("Langfuse must not be created without keys")

    monkeypatch.setattr(tracing, "Langfuse", forbidden)
    monkeypatch.setattr(tracing, "CallbackHandler", forbidden)

    assert tracing.tracing_callbacks(settings(**langfuse)) == []


def test_with_keys_the_client_and_langchain_handler_are_created(monkeypatch: pytest.MonkeyPatch) -> None:
    created: list[tuple[str, dict[str, Any]]] = []

    class Recorder(BaseCallbackHandler):
        def __init__(self, kind: str, **kwargs: Any) -> None:
            created.append((kind, kwargs))

    monkeypatch.setattr(tracing, "Langfuse", lambda **kwargs: Recorder("client", **kwargs))
    monkeypatch.setattr(tracing, "CallbackHandler", lambda **kwargs: Recorder("handler", **kwargs))
    configured = settings(
        langfuse_public_key="pk-lf-test",
        langfuse_secret_key=SecretStr("sk-lf-test"),
        langfuse_host="https://cloud.langfuse.com",
    )

    [handler] = tracing.tracing_callbacks(configured)

    assert isinstance(handler, Recorder)
    assert created == [
        ("client", {"public_key": "pk-lf-test", "secret_key": "sk-lf-test", "base_url": "https://cloud.langfuse.com"}),
        ("handler", {"public_key": "pk-lf-test"}),
    ]
