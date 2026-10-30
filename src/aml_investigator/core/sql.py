from functools import cache
from importlib import resources


@cache
def load(package: str, name: str) -> str:
    return resources.files(package).joinpath("sql", f"{name}.sql").read_text()
