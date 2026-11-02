from datetime import date, timedelta

import pytest

from aml_investigator.investigation.routing import QA_RATE, qa_sampled, route_for

DAY = date(2022, 9, 7)


def test_qa_draw_is_reproducible() -> None:
    keys = [(f"ACC{index}", DAY + timedelta(days=index % 5)) for index in range(500)]

    assert [qa_sampled(*key) for key in keys] == [qa_sampled(*key) for key in keys]


def test_qa_draw_samples_about_three_percent() -> None:
    draws = [qa_sampled(f"ACC{index:05d}", DAY + timedelta(days=index % 7)) for index in range(20_000)]

    assert sum(draws) / len(draws) == pytest.approx(QA_RATE, abs=0.005)


def test_qa_draw_depends_on_account_and_date() -> None:
    sampled = next(f"ACC{index}" for index in range(10_000) if qa_sampled(f"ACC{index}", DAY))

    assert not all(qa_sampled(sampled, DAY + timedelta(days=offset)) for offset in range(1, 30))


@pytest.mark.parametrize(
    ("recommendation", "status"), [("escalate", "passed"), ("escalate", "failed"), ("close", "failed")]
)
def test_everything_but_a_verified_close_is_escalated(recommendation: str, status: str) -> None:
    assert route_for(recommendation, status, "ACC1", DAY) == "escalated"  # type: ignore[arg-type]


def test_verified_close_is_auto_closed_or_sampled() -> None:
    routes = {route_for("close", "passed", f"ACC{index}", DAY) for index in range(1_000)}

    assert routes == {"auto_closed", "qa_sample"}
    assert route_for("close", "passed", "ACC1", DAY) == ("qa_sample" if qa_sampled("ACC1", DAY) else "auto_closed")
