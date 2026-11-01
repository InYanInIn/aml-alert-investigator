from datetime import date
from pathlib import Path

from aml_investigator.core.errors import DomainError

TEST_SPLIT_LOG = Path("docs/test-split-log.md")
MILESTONE = "M3"


class ClosedTestSplitError(DomainError):
    def __init__(self) -> None:
        super().__init__("the test split needs a reason; every opening is logged")


def require_reason(split: str, test_reason: str | None) -> None:
    if split == "test" and not test_reason:
        raise ClosedTestSplitError


def record_opening(workspace: Path, *, computed: str, reason: str) -> None:
    log = workspace / TEST_SPLIT_LOG
    lines = log.read_text().splitlines()
    last_row = max(index for index, line in enumerate(lines) if line.startswith("|"))
    row = f"| {date.today().isoformat()} | {MILESTONE} | {table_cell(computed)} Reason: {table_cell(reason)} | None. |"
    lines.insert(last_row + 1, row)
    log.write_text("\n".join(lines) + "\n")


def table_cell(text: str) -> str:
    return " ".join(text.split()).replace("|", "\\|")
