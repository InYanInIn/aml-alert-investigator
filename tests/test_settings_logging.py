import json
import logging
from collections.abc import Iterator
from typing import Any

import pytest
from pydantic import SecretStr

from aml_investigator.core.logging import configure_logging, log_context
from aml_investigator.core.settings import Settings

PASSWORD = "s3cret-Passw0rd"  # noqa: S105


def dsn(role: str) -> SecretStr:
    return SecretStr(f"postgresql://{role}:{PASSWORD}@127.0.0.1:5433/aml")


@pytest.fixture
def settings() -> Settings:
    return Settings(
        owner_dsn=dsn("aml"), mcp_dsn=dsn("aml_mcp_reader"), app_dsn=dsn("aml_app"), evaluator_dsn=dsn("aml_evaluator")
    )


@pytest.fixture
def json_logs() -> Iterator[logging.Logger]:
    root = logging.getLogger()
    handlers, level = list(root.handlers), root.level
    yield logging.getLogger("aml_investigator.test")
    root.handlers, root.level = handlers, level


def logged_lines(capsys: pytest.CaptureFixture[str]) -> list[dict[str, Any]]:
    return [json.loads(line) for line in capsys.readouterr().err.splitlines()]


def test_settings_never_show_passwords(settings: Settings) -> None:
    retargeted = settings.with_database("aml_hi_small")

    for shown in (repr(settings), str(settings), repr(retargeted), str(retargeted), settings.model_dump_json()):
        assert PASSWORD not in shown
    assert retargeted.database == "aml_hi_small"
    assert PASSWORD in retargeted.app_conninfo


def test_logged_settings_never_show_passwords(
    settings: Settings, json_logs: logging.Logger, capsys: pytest.CaptureFixture[str]
) -> None:
    configure_logging("INFO")
    json_logs.info("starting with %s", settings)

    [line] = logged_lines(capsys)
    assert "aml_app" not in line["message"]
    assert PASSWORD not in json.dumps(line)


def test_log_lines_are_json_with_context_only_inside(
    json_logs: logging.Logger, capsys: pytest.CaptureFixture[str]
) -> None:
    configure_logging("INFO")
    json_logs.info("before")
    with log_context(alert_id=7, thread_id="7:abc"):
        json_logs.info("inside", extra={"fields": {"duration_ms": 1.5}})
        with log_context(investigation_id=3):
            json_logs.warning("nested")
    json_logs.info("after")

    before, inside, nested, after = logged_lines(capsys)
    assert {"ts", "level", "logger", "message"} <= before.keys()
    assert (inside["message"], inside["level"], inside["logger"]) == ("inside", "INFO", "aml_investigator.test")
    assert (inside["alert_id"], inside["thread_id"], inside["duration_ms"]) == (7, "7:abc", 1.5)
    assert (nested["alert_id"], nested["thread_id"], nested["investigation_id"]) == (7, "7:abc", 3)
    for outside in (before, after):
        assert "alert_id" not in outside
        assert "thread_id" not in outside
    assert "investigation_id" not in inside
