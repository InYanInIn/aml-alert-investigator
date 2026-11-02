from dataclasses import dataclass


@dataclass(frozen=True)
class Passage:
    passage_id: str
    article: str
    paragraph: str | None
    position: int
    heading: str | None
    text: str
