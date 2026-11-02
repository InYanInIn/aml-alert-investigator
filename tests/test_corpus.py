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
    article_2 = polish_act()["PL-AML art. 2 ust. 1"].text
    assert "krajowe instytucje płatnicze, krajowe instytucje pieniądza elektronicznego, oddziały" in article_2
    assert "kasach oszczędnościowo-kredytowych" in article_2
    assert "w rozumieniu ustawy" in article_2
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
