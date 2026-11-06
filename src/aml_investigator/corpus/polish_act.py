import re
from collections import Counter
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path

from pypdf import PageObject, PdfReader

from aml_investigator.corpus.passage import Passage, split_points

BODY_FONT_SIZE_MIN = 9.5
BODY_TOP = 785.0
BODY_BOTTOM = 55.0
ACT_START = re.compile(r"^Art\. 1\. ", re.MULTILINE)
ARTICLE_START = re.compile(r"^Art\. (\d+)([a-z]*)(?:\u2013\d+[a-z]*)?\. ?(.*)$")
PARAGRAPH_START = re.compile(r"^(\d+)([a-z]*)\. (.*)$")
CHAPTER_HEADING = re.compile(r"^Rozdział \d+[a-z]*$")
LIST_ITEM = re.compile(r"^(\d+[a-z]*\)|[a-z]\)|\u2013 )")
LINE_END_HYPHEN = re.compile(r"(\w+) ?-$")
SPACED_HYPHEN = re.compile(r"(?<=\w) +-(?=\w)")
SPACE_BEFORE_PUNCTUATION = re.compile(r" +(?=[,;)])")
WORD = re.compile(r"\w+")
COMPOUND = re.compile(r"\w+-\w+")
LETTER_PAIR = re.compile(r"(?<!\w)([^\W\d_]+) (?=([^\W\d_]+)(?!\w))")
STANDALONE_LETTERS = set("aiouwz")
POINT_LISTS = {("2", "1"), ("2", "2")}
REPEALED = {"(uchylony)", "(uchylona)", "(uchylone)", "(pominięte)"}


def parse_polish_act(path: Path) -> list[Passage]:
    lines = [SPACED_HYPHEN.sub("-", line) for line in act_lines(PdfReader(path))]
    compounds = {word for line in lines for word in COMPOUND.findall(line)}
    drafts = [(unit, joined(unit.lines, compounds)) for unit in units(lines)]
    repair = split_word_repair([text for _, text in drafts])
    kept = [(unit, repair(text)) for unit, text in drafts if text not in REPEALED]
    return [
        Passage(
            f"PL-AML art. {unit.article}"
            + (f" ust. {unit.paragraph}" if unit.paragraph else "")
            + (f" pkt {point}" if point else ""),
            unit.article,
            unit.paragraph,
            position,
            None,
            text,
        )
        for position, (unit, point, text) in enumerate(with_points(kept), start=1)
    ]


def act_lines(reader: PdfReader) -> list[str]:
    text = "\n".join(body_text(page) for page in reader.pages)
    start = ACT_START.search(text)
    if start is None:
        raise ValueError("the PDF has no line starting with 'Art. 1.'")
    lines = [" ".join(line.split()) for line in text[start.start() :].splitlines()]
    return [line for line in lines if line]


def body_text(page: PageObject) -> str:
    parts: list[str] = []

    def keep_body(text: str, cm: list[float], tm: list[float], _font: object, size: float) -> None:
        scale = tm[3] * cm[3]
        baseline = tm[4] * cm[1] + tm[5] * cm[3] + cm[5]
        is_body = abs(size * scale) >= BODY_FONT_SIZE_MIN and BODY_BOTTOM < baseline < BODY_TOP
        parts.append(text if is_body else "\n" * text.count("\n"))

    page.extract_text(visitor_text=keep_body)
    return "".join(parts)


@dataclass
class Unit:
    article: str
    paragraph: str | None
    lines: list[str]


def with_points(units_with_text: list[tuple[Unit, str]]) -> Iterator[tuple[Unit, str | None, str]]:
    for unit, text in units_with_text:
        if (unit.article, unit.paragraph) in POINT_LISTS:
            for point, body in split_points(text):
                yield unit, point, body
        else:
            yield unit, None, text


def units(lines: list[str]) -> list[Unit]:
    found: list[Unit] = []
    last_article = (0, "")
    last_paragraph: tuple[int, str] | None = None
    in_chapter_heading = False
    for line in lines:
        if CHAPTER_HEADING.match(line):
            in_chapter_heading = True
            continue
        article = ARTICLE_START.match(line)
        if article and number_key(article) > last_article:
            in_chapter_heading = False
            last_article = number_key(article)
            rest = article.group(3)
            first_paragraph = PARAGRAPH_START.match(rest)
            if first_paragraph and number_key(first_paragraph) == (1, ""):
                found.append(Unit(article_label(article), "1", [first_paragraph.group(3)]))
                last_paragraph = (1, "")
            else:
                found.append(Unit(article_label(article), None, [rest]))
                last_paragraph = None
            continue
        if in_chapter_heading:
            continue
        paragraph = PARAGRAPH_START.match(line)
        if paragraph and last_paragraph and follows(last_paragraph, number_key(paragraph)):
            last_paragraph = number_key(paragraph)
            found.append(Unit(found[-1].article, paragraph.group(1) + paragraph.group(2), [paragraph.group(3)]))
            continue
        found[-1].lines.append(line)
    return found


def number_key(match: re.Match[str]) -> tuple[int, str]:
    return int(match.group(1)), match.group(2)


def article_label(match: re.Match[str]) -> str:
    return match.group(1) + match.group(2)


def follows(previous: tuple[int, str], candidate: tuple[int, str]) -> bool:
    return candidate > previous and candidate[0] in (previous[0], previous[0] + 1)


def joined(lines: list[str], compounds: set[str]) -> str:
    text = ""
    for line in filter(None, lines):
        hyphen = LINE_END_HYPHEN.search(text)
        if hyphen and line.startswith("-"):
            text = text[: hyphen.start()] + hyphen.group(1) + line
        elif hyphen and line[0].islower():
            stem, tail = hyphen.group(1), WORD.match(line)
            joiner = "-" if tail and f"{stem}-{tail.group()}" in compounds else ""
            text = text[: hyphen.start()] + stem + joiner + line
        elif not text:
            text = line
        elif LIST_ITEM.match(line):
            text += "\n" + line
        else:
            text += " " + line
    return text


def split_word_repair(texts: list[str]) -> Callable[[str], str]:
    words = Counter(word for text in texts for word in WORD.findall(text))
    pairs = Counter(letter_pair(match) for text in texts for match in LETTER_PAIR.finditer(text))
    splits = {pair for pair, occurrences in pairs.items() if is_split_word(*pair, occurrences, words)}

    def repair(text: str) -> str:
        merged = LETTER_PAIR.sub(lambda match: match.group(1) + ("" if letter_pair(match) in splits else " "), text)
        return SPACE_BEFORE_PUNCTUATION.sub("", merged)

    return repair


def letter_pair(match: re.Match[str]) -> tuple[str, str]:
    return match.group(1), match.group(2)


def is_split_word(first: str, second: str, occurrences: int, words: Counter[str]) -> bool:
    joined_count = words[first + second]
    fragments = [is_fragment(part, occurrences, words) for part in (first, second)]
    if joined_count < occurrences:
        return False
    return (
        first == "nie"
        or all(fragments)
        or (len(first) == 1 and fragments[1])
        or (joined_count > occurrences and any(fragments))
    )


def is_fragment(part: str, occurrences: int, words: Counter[str]) -> bool:
    return words[part] <= occurrences or (len(part) == 1 and part not in STANDALONE_LETTERS)
