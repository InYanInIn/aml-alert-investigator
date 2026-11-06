import argparse

import pytest

from aml_investigator.cli.evaluation import positive_int, run_evaluation


def test_positive_int_accepts_positive_numbers() -> None:
    assert positive_int("3") == 3


@pytest.mark.parametrize("text", ["0", "-2"])
def test_positive_int_rejects_zero_and_negatives(text: str) -> None:
    with pytest.raises(argparse.ArgumentTypeError):
        positive_int(text)


def test_local_model_is_refused_on_the_test_split() -> None:
    args = argparse.Namespace(system="local", split="test", open_test=True, reason="why", limit=None, concurrency=1)

    with pytest.raises(SystemExit, match="ADR-0022"):
        run_evaluation(args)
