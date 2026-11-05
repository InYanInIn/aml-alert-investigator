import argparse
from pathlib import Path

from aml_investigator.core.settings import Settings, load_settings

THRESHOLDS_DIR = Path("config/thresholds")

type Commands = argparse._SubParsersAction[argparse.ArgumentParser]


def settings_for(args: argparse.Namespace) -> Settings:
    settings = load_settings()
    return settings.with_database(args.database) if args.database else settings
