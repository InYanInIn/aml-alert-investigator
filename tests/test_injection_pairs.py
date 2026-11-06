import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

import pytest

from aml_investigator.core.settings import Settings
from aml_investigator.evaluation.injection_pairs import evaluate_injection_pairs
from aml_investigator.evaluation.systems import EvalAlert, SystemDecision
from aml_investigator.mcp_server.schemas import ReferenceVariant
from tests.test_injections import make_set, settings  # noqa: F401

LIMIT = 4


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


class FooledByInjections:
    name = "fooled"

    def __init__(self, variant: ReferenceVariant, opened: list[ReferenceVariant]) -> None:
        self.variant = variant
        opened.append(variant)

    async def decide(self, alert: EvalAlert) -> SystemDecision:
        fooled = self.variant == "injected" and alert.account_id.endswith(("0", "2", "4", "6", "8"))
        return SystemDecision("close" if fooled else "escalate", verification_status="passed", trace={"tokens": 1})


@pytest.mark.anyio
async def test_pairs_run_each_alert_clean_then_injected(settings: Settings, tmp_path: Path) -> None:  # noqa: F811
    injections = make_set(settings, tmp_path)["alerts"][:LIMIT]
    opened: list[ReferenceVariant] = []

    @asynccontextmanager
    async def open_variant(variant: ReferenceVariant) -> AsyncIterator[FooledByInjections]:
        yield FooledByInjections(variant, opened)

    path = await evaluate_injection_pairs(settings, open_variant, limit=LIMIT, concurrency=1, workspace=tmp_path)

    assert path == tmp_path / "eval" / "results" / settings.database / "val" / "injection-pairs" / "fooled-first4.json"
    assert opened == ["clean", "injected"]
    document = json.loads(path.read_text())
    pairs = document["pairs"]
    assert [pair["injection_id"] for pair in pairs] == [alert["injection_id"] for alert in injections]
    expected = [alert["account_id"].endswith(("0", "2", "4", "6", "8")) for alert in injections]
    assert [pair["attack_succeeded"] for pair in pairs] == expected
    assert {pair["clean"]["recommendation"] for pair in pairs} == {"escalate"}
    assert document["counts"] == {
        "pairs": LIMIT,
        "escalated_clean": LIMIT,
        "closed_clean": 0,
        "closed_injected": sum(expected),
        "attack_succeeded": sum(expected),
    }
    assert pairs[0]["injected"]["trace"] == {"tokens": 1}
    assert len(document["injections_sha256"]) == 64


@pytest.mark.anyio
async def test_control_runs_the_clean_variant_twice_under_its_own_name(settings: Settings, tmp_path: Path) -> None:  # noqa: F811
    make_set(settings, tmp_path)
    opened: list[ReferenceVariant] = []

    @asynccontextmanager
    async def open_variant(variant: ReferenceVariant) -> AsyncIterator[FooledByInjections]:
        yield FooledByInjections(variant, opened)

    path = await evaluate_injection_pairs(
        settings, open_variant, limit=LIMIT, concurrency=1, workspace=tmp_path, control=True
    )

    assert path.name == "fooled-first4-control.json"
    assert opened == ["clean", "clean"]
    document = json.loads(path.read_text())
    assert document["control"] is True
    assert document["counts"]["attack_succeeded"] == 0
