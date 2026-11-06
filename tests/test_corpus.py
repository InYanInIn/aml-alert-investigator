import json
import re
from functools import cache
from pathlib import Path

from aml_investigator.corpus.amlr import parse_amlr
from aml_investigator.corpus.loader import load_corpus
from aml_investigator.corpus.passage import Passage
from aml_investigator.corpus.polish_act import parse_polish_act
from aml_investigator.corpus.sources import verified_sources
from tests.conftest import Database

SOURCES = Path("corpus/sources")
QUERIES = Path("eval/retrieval/queries.json")
HYPHEN_LINE_BREAK = re.compile(r"\w-(?: |\n)\w|\w -\w")


@cache
def amlr() -> dict[str, Passage]:
    return {passage.passage_id: passage for passage in parse_amlr(SOURCES / "amlr-2024-1624.en.xhtml")}


@cache
def polish_act() -> dict[str, Passage]:
    return {passage.passage_id: passage for passage in parse_polish_act(SOURCES / "pl-aml-2025-644.pdf")}


def all_passages() -> list[Passage]:
    return [*amlr().values(), *polish_act().values()]


def test_committed_sources_match_manifest() -> None:
    assert [record.doc_id for record in verified_sources(SOURCES)] == ["AMLR", "PL-AML"]


def test_amlr_has_all_90_articles() -> None:
    assert {passage.article for passage in amlr().values()} == {str(number) for number in range(1, 91)}


def test_amlr_paragraph_passages_keep_their_points() -> None:
    record_retention = amlr()["AMLR art. 77(1)"]
    assert "record of the assessment" in record_retention.text
    assert record_retention.heading == "Record retention"
    assert "meaningful human intervention" in amlr()["AMLR art. 76(5)"].text


def test_amlr_article_without_paragraphs_is_one_passage() -> None:
    assert amlr()["AMLR art. 3"].paragraph is None
    assert not any(passage_id.startswith("AMLR art. 3(") for passage_id in amlr())


def test_polish_paragraph_passages() -> None:
    assert "zawiadamia Generalnego Inspektora" in polish_act()["PL-AML art. 74 ust. 1"].text
    assert "15 000 euro" in polish_act()["PL-AML art. 72 ust. 1"].text
    assert polish_act()["PL-AML art. 67"].text == "Rejestr jest jawny."


def test_amlr_definitions_are_one_passage_per_point() -> None:
    points = [passage for passage_id, passage in amlr().items() if passage_id.startswith("AMLR art. 2(1)(")]
    assert "AMLR art. 2(1)" not in amlr()
    assert [passage.passage_id for passage in points] == [f"AMLR art. 2(1)({number})" for number in range(1, 58)]
    assert {(passage.article, passage.paragraph, passage.heading) for passage in points} == {("2", "1", "Definitions")}
    assert amlr()["AMLR art. 2(1)(1)"].text.startswith(
        "\u2018money laundering\u2019 means the conduct set out in Article 3"
    )
    assert amlr()["AMLR art. 2(1)(28)"].text.startswith("\u2018beneficial owner\u2019 means any natural person")
    assert "(a) a credit institution as defined" in amlr()["AMLR art. 2(1)(5)"].text
    assert "(b) a branch of a credit institution" in amlr()["AMLR art. 2(1)(5)"].text
    assert "(6)" not in amlr()["AMLR art. 2(1)(5)"].text
    assert max(len(passage.text) for passage in points) <= 4000


def test_polish_definitions_are_one_passage_per_point() -> None:
    points = [
        passage for passage_id, passage in polish_act().items() if passage_id.startswith("PL-AML art. 2 ust. 2 pkt ")
    ]
    assert "PL-AML art. 2 ust. 2" not in polish_act()
    assert len(points) == 36
    assert {(passage.article, passage.paragraph) for passage in points} == {("2", "2")}
    assert polish_act()["PL-AML art. 2 ust. 2 pkt 1"].text.startswith(
        "beneficjencie rzeczywistym \u2013 rozumie si\u0119 przez to"
    )
    assert "b) w przypadku trustu:" in polish_act()["PL-AML art. 2 ust. 2 pkt 1"].text
    assert polish_act()["PL-AML art. 2 ust. 2 pkt 12a"].text.startswith("pa\u0144stwie cz\u0142onkowskim")
    assert "pkt 1a" not in polish_act()["PL-AML art. 2 ust. 2 pkt 1"].text
    assert max(len(passage.text) for passage in points) <= 4000


def test_polish_obliged_entities_are_one_passage_per_point() -> None:
    points = [
        passage for passage_id, passage in polish_act().items() if passage_id.startswith("PL-AML art. 2 ust. 1 pkt ")
    ]
    assert "PL-AML art. 2 ust. 1" not in polish_act()
    assert len(points) == 31
    assert polish_act()["PL-AML art. 2 ust. 1 pkt 1"].text.startswith("banki krajowe")
    assert "e) tworzenia, prowadzenia" in polish_act()["PL-AML art. 2 ust. 1 pkt 14"].text
    assert "pkt 15" not in polish_act()["PL-AML art. 2 ust. 1 pkt 14"].text


def test_no_passage_exceeds_4000_characters() -> None:
    assert max(len(passage.text) for passage in all_passages()) <= 4000


def test_polish_articles_with_letters_parse() -> None:
    assert polish_act()["PL-AML art. 129ka ust. 1"].article == "129ka"
    assert "PL-AML art. 11a ust. 1" in polish_act()


def test_polish_text_has_no_page_furniture_or_footnotes() -> None:
    text = "\n".join(passage.text for passage in polish_act().values())
    assert "Dziennik Ustaw" not in text
    assert "2025-05-19" not in text
    assert "Niniejsza ustawa" not in text
    assert "(uchylony)" not in {passage.text for passage in polish_act().values()}


def test_polish_text_repairs_split_words() -> None:
    assert (
        "krajowe instytucje płatnicze, krajowe instytucje pieniądza elektronicznego, oddziały"
        in polish_act()["PL-AML art. 2 ust. 1 pkt 3"].text
    )
    assert "kasach oszczędnościowo-kredytowych" in polish_act()["PL-AML art. 2 ust. 1 pkt 2"].text
    assert "w rozumieniu ustawy" in polish_act()["PL-AML art. 2 ust. 1 pkt 1"].text
    assert "spółdzielczych kas oszczędnościowo-kredytowych" in polish_act()["PL-AML art. 130 ust. 2"].text


def test_no_hyphenated_line_break_artefacts() -> None:
    offenders = [passage.passage_id for passage in all_passages() if HYPHEN_LINE_BREAK.search(passage.text)]
    assert offenders == []


def test_passage_ids_and_positions_are_unique() -> None:
    for parsed in (parse_amlr(SOURCES / "amlr-2024-1624.en.xhtml"), parse_polish_act(SOURCES / "pl-aml-2025-644.pdf")):
        assert len({passage.passage_id for passage in parsed}) == len(parsed)
        assert [passage.position for passage in parsed] == list(range(1, len(parsed) + 1))


def corpus_rows(db: Database) -> list[tuple[object, ...]]:
    with db.owner() as conn:
        return conn.execute(
            "SELECT passage_id, doc_id, position, content_sha256, search_config::text FROM corpus.passages "
            "ORDER BY doc_id, position"
        ).fetchall()


def test_load_is_idempotent(db: Database) -> None:
    with db.owner() as conn:
        first = load_corpus(conn, SOURCES)
    rows = corpus_rows(db)
    with db.owner() as conn:
        second = load_corpus(conn, SOURCES)
        documents = conn.execute("SELECT doc_id FROM corpus.documents ORDER BY doc_id").fetchall()

    assert first == second
    assert first.passages_per_document == {"AMLR": len(amlr()), "PL-AML": len(polish_act())}
    assert corpus_rows(db) == rows
    assert documents == [("AMLR",), ("PL-AML",)]
    assert {row[4] for row in rows} == {"english", "simple"}


def test_every_expected_query_passage_exists_after_loading(db: Database) -> None:
    with db.owner() as conn:
        load_corpus(conn, SOURCES)
        loaded = {row[0] for row in conn.execute("SELECT passage_id FROM corpus.passages").fetchall()}
    queries = json.loads(QUERIES.read_text())["queries"]
    expected = {passage_id for query in queries for passage_id in query["expected"]}

    assert len(queries) >= 20
    assert sum(query["polish_only"] for query in queries) >= 8
    assert expected - loaded == set()
