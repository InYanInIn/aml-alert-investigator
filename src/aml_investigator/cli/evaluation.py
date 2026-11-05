import argparse
import asyncio

from aml_investigator.cli.context import Commands, settings_for


def test_reason(args: argparse.Namespace) -> str | None:
    if args.split != "test":
        return None
    if not args.open_test or not args.reason:
        raise SystemExit("the test split needs --open-test and --reason; every opening is logged")
    return str(args.reason)


def run_injections(args: argparse.Namespace) -> None:
    from aml_investigator.evaluation.injections import create_injection_set

    print(f"injection set written to {create_injection_set(settings_for(args), size=args.size, seed=args.seed)}")


def run_sample(args: argparse.Namespace) -> None:
    from aml_investigator.evaluation.samples import create_sample

    path = create_sample(
        settings_for(args), split=args.split, size=args.size, seed=args.seed, test_reason=test_reason(args)
    )
    print(f"sample written to {path}")


def run_evaluation(args: argparse.Namespace) -> None:
    from aml_investigator.evaluation.runner import evaluate

    result = asyncio.run(
        evaluate(settings_for(args), system=args.system, split=args.split, test_reason=test_reason(args))
    )
    print(result.summary())


def register(commands: Commands) -> None:
    evaluation = commands.add_parser("eval").add_subparsers(required=True)

    injections = evaluation.add_parser("injections")
    injections.add_argument("--size", type=int, default=40)
    injections.add_argument("--seed", type=int, default=20261102)
    injections.set_defaults(handler=run_injections)

    sample = evaluation.add_parser("sample")
    sample.add_argument("--split", choices=["val", "test"], required=True)
    sample.add_argument("--size", type=int)
    sample.add_argument("--seed", type=int, default=20261030)
    sample.add_argument("--open-test", action="store_true")
    sample.add_argument("--reason")
    sample.set_defaults(handler=run_sample)

    run = evaluation.add_parser("run")
    run.add_argument("--system", required=True)
    run.add_argument("--split", choices=["val", "test"], required=True)
    run.add_argument("--open-test", action="store_true")
    run.add_argument("--reason")
    run.set_defaults(handler=run_evaluation)
