import re
from dataclasses import dataclass

POINT_START = re.compile(r"^\(?(\d+[a-z]*)\) ", re.MULTILINE)


@dataclass(frozen=True)
class Passage:
    passage_id: str
    article: str
    paragraph: str | None
    position: int
    heading: str | None
    text: str


def split_points(text: str) -> list[tuple[str, str]]:
    starts = list(POINT_START.finditer(text))
    ends = [start.start() for start in starts[1:]] + [len(text)]
    return [(start.group(1), text[start.end() : end].rstrip("\n")) for start, end in zip(starts, ends, strict=True)]
