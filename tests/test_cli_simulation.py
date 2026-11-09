import argparse
from types import SimpleNamespace

import pytest

from aml_investigator.cli.simulation import SIM_SYSTEMS, register, run_simulation


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


def test_sim_run_rejects_a_negative_seed() -> None:
    with pytest.raises(SystemExit):
        parser().parse_args(["sim", "run", "--seed", "-1", "--system", "b4", "--cadence", "1"])


def test_sim_run_accepts_seed_zero() -> None:
    args = parser().parse_args(["sim", "run", "--seed", "0", "--system", "b4", "--cadence", "1"])

    assert args.seed == 0


def test_sim_run_checks_the_world_before_simulating(monkeypatch: pytest.MonkeyPatch) -> None:
    simulated: list[bool] = []

    async def simulate(*_: object, **__: object) -> None:
        simulated.append(True)
        raise RuntimeError("simulate must not run")

    monkeypatch.setattr(
        "aml_investigator.cli.simulation.settings_for", lambda _: SimpleNamespace(database="aml_sim_missing")
    )
    monkeypatch.setattr("aml_investigator.simulation.loop.simulate", simulate)
    args = argparse.Namespace(seed=1, system="b4", cadence=1, limit=60)

    with pytest.raises(FileNotFoundError, match="aml_sim_missing"):
        run_simulation(args)
    assert simulated == []
