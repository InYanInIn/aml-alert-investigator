import argparse

import pytest

from aml_investigator.cli.simulation import SIM_SYSTEMS, register


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(prog="aml")
    root.add_argument("--database")
    register(root.add_subparsers(required=True))
    return root


def test_sim_run_parses_the_documented_arguments() -> None:
    args = parser().parse_args(["sim", "run", "--seed", "7", "--system", "b4", "--cadence", "3"])

    assert (args.seed, args.system, args.cadence, args.limit) == (7, "b4", 3, 60)


def test_b3_is_not_a_simulation_system() -> None:
    assert "b3" not in SIM_SYSTEMS
    with pytest.raises(SystemExit):
        parser().parse_args(["sim", "run", "--seed", "7", "--system", "b3", "--cadence", "1"])


def test_only_the_three_compared_cadences_exist() -> None:
    with pytest.raises(SystemExit):
        parser().parse_args(["sim", "run", "--seed", "7", "--system", "b4", "--cadence", "2"])
