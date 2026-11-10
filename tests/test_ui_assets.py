import argparse

import pytest
from fastapi.testclient import TestClient

from aml_investigator.cli import serve
from aml_investigator.core.settings import Settings
from aml_investigator.investigation.models.rule_score import RuleScoreChatModel
from aml_investigator.web import app as web_app


def test_ui_cli_requires_built_assets_before_database_work(monkeypatch: pytest.MonkeyPatch, tmp_path: object) -> None:
    monkeypatch.setattr(web_app, "WORLD_STATIC_DIR", tmp_path)
    monkeypatch.setattr(serve, "settings_for", lambda _: pytest.fail("database settings read before asset check"))
    args = argparse.Namespace(database=None, port=8765, evaluator=False, no_browser=True)

    with pytest.raises(SystemExit, match="Run make ui"):
        serve.run_ui(args)


def test_world_page_explains_missing_build(monkeypatch: pytest.MonkeyPatch, tmp_path: object) -> None:
    monkeypatch.setattr(web_app, "WORLD_STATIC_DIR", tmp_path)
    app = web_app.create_app(
        Settings(),
        mcp="http://127.0.0.1:9/mcp",
        model=RuleScoreChatModel(thresholds={}, close_below=0),
        ui_mode=True,
    )

    response = TestClient(app).get("/world")

    assert response.status_code == 503
    assert "Run make ui" in response.text
