import re
import xml.etree.ElementTree as ET
from collections.abc import Iterator
from pathlib import Path

from aml_investigator.corpus.passage import Passage, split_points

XHTML = "{http://www.w3.org/1999/xhtml}"
ARTICLE_ID = re.compile(r"art_(\d+)")
PARAGRAPH_ID = re.compile(r"\d{3}\.(\d{3})")
PARAGRAPH_NUMBER = re.compile(r"(\d+)\.\s+")
HEADING_CLASSES = {"oj-ti-art", "eli-title"}
SPACE_BEFORE_PUNCTUATION = re.compile(r" (?=[,.;:)])")
DEFINITIONS = ("2", "1")


def parse_amlr(path: Path) -> list[Passage]:
    root = ET.parse(path).getroot()  # noqa: S314
    articles = [
        (match.group(1), div) for div in root.iter(f"{XHTML}div") if (match := ARTICLE_ID.fullmatch(div.get("id", "")))
    ]
    return [
        Passage(
            f"AMLR art. {article}" + (f"({paragraph})" if paragraph else "") + (f"({point})" if point else ""),
            article,
            paragraph,
            position,
            heading,
            text,
        )
        for position, (article, paragraph, point, heading, text) in enumerate(
            (passage for article, div in articles for passage in article_passages(article, div)), start=1
        )
    ]


def article_passages(article: str, div: ET.Element) -> Iterator[tuple[str, str | None, str | None, str | None, str]]:
    heading = article_heading(div)
    paragraphs = [
        (str(int(match.group(1))), child) for child in div if (match := PARAGRAPH_ID.fullmatch(child.get("id", "")))
    ]
    if not paragraphs:
        body = [child for child in div if child.get("class") not in HEADING_CLASSES]
        yield article, None, None, heading, block_text(body)
    for paragraph, child in paragraphs:
        text = without_paragraph_number(block_text(list(child)), article, paragraph)
        if (article, paragraph) == DEFINITIONS:
            for point, definition in split_points(text):
                yield article, paragraph, point, heading, definition
        else:
            yield article, paragraph, None, heading, text


def article_heading(div: ET.Element) -> str | None:
    titles = [inline_text(p) for p in div.iter(f"{XHTML}p") if p.get("class") == "oj-sti-art"]
    return titles[0] if titles else None


def without_paragraph_number(text: str, article: str, paragraph: str) -> str:
    match = PARAGRAPH_NUMBER.match(text)
    if match is None or match.group(1) != paragraph:
        raise ValueError(f"AMLR art. {article}({paragraph}) does not start with its paragraph number")
    return text[match.end() :]


def block_text(elements: list[ET.Element]) -> str:
    return "\n".join(line for element in elements for line in block_lines(element))


def block_lines(element: ET.Element) -> Iterator[str]:
    tag = element.tag.removeprefix(XHTML)
    if tag == "p":
        yield inline_text(element)
    elif tag == "table":
        for row in element.findall(f"{XHTML}tbody/{XHTML}tr") + element.findall(f"{XHTML}tr"):
            yield from row_lines(row)
    else:
        for child in element:
            yield from block_lines(child)


def row_lines(row: ET.Element) -> Iterator[str]:
    label_cell, *content_cells = list(row)
    label = " ".join(block_lines(label_cell))
    content = [line for cell in content_cells for line in block_lines(cell)]
    if not content:
        yield label
        return
    yield f"{label} {content[0]}"
    yield from content[1:]


def inline_text(element: ET.Element) -> str:
    return SPACE_BEFORE_PUNCTUATION.sub("", " ".join("".join(inline_parts(element)).split()))


def inline_parts(element: ET.Element) -> Iterator[str]:
    yield element.text or ""
    for child in element:
        if not is_footnote_reference(child):
            yield from inline_parts(child)
        yield child.tail or ""


def is_footnote_reference(element: ET.Element) -> bool:
    return element.tag == f"{XHTML}a" and element.get("href", "").startswith("#ntr")
