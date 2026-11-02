import hashlib
from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass
from pathlib import Path

import psycopg

from aml_investigator.core.sql import load
from aml_investigator.corpus.amlr import parse_amlr
from aml_investigator.corpus.passage import Passage
from aml_investigator.corpus.polish_act import parse_polish_act
from aml_investigator.corpus.sources import SourceRecord, verified_sources

UPSERT_DOCUMENT_SQL = load(__package__, "upsert_document")
DELETE_PASSAGES_SQL = load(__package__, "delete_passages")
INSERT_PASSAGE_SQL = load(__package__, "insert_passage")
PARSERS: Mapping[str, Callable[[Path], list[Passage]]] = {"AMLR": parse_amlr, "PL-AML": parse_polish_act}
SEARCH_CONFIGS = {"en": "english", "pl": "simple"}


@dataclass(frozen=True)
class CorpusReport:
    passages_per_document: Mapping[str, int]

    def __str__(self) -> str:
        lines = [f"{doc_id}: {count} passages" for doc_id, count in self.passages_per_document.items()]
        return f"documents: {len(self.passages_per_document)}\n" + "\n".join(lines)


def load_corpus(conn: psycopg.Connection[tuple[object, ...]], directory: Path) -> CorpusReport:
    counts = {
        record.doc_id: load_document(conn, record, directory / record.file) for record in verified_sources(directory)
    }
    return CorpusReport(counts)


def load_document(conn: psycopg.Connection[tuple[object, ...]], record: SourceRecord, path: Path) -> int:
    passages = PARSERS[record.doc_id](path)
    search_config = SEARCH_CONFIGS[record.language]
    with conn.transaction(), conn.cursor() as cursor:
        cursor.execute(DELETE_PASSAGES_SQL, {"doc_id": record.doc_id})
        cursor.execute(UPSERT_DOCUMENT_SQL, document_row(record))
        cursor.executemany(
            INSERT_PASSAGE_SQL,
            [
                asdict(passage)
                | {
                    "doc_id": record.doc_id,
                    "content_sha256": hashlib.sha256(passage.text.encode()).hexdigest(),
                    "search_config": search_config,
                }
                for passage in passages
            ],
        )
    return len(passages)


def document_row(record: SourceRecord) -> dict[str, str]:
    return {
        "doc_id": record.doc_id,
        "title": record.title,
        "language": record.language,
        "version": record.version,
        "source_url": record.source_url,
        "content_sha256": record.sha256,
    }
