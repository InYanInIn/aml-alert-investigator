import hashlib
import json
import ssl
import urllib.request
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path

import truststore

MANIFEST = "sources.json"
EUR_LEX_LICENCE = (
    "© European Union, https://eur-lex.europa.eu/. Reused under Commission Decision 2011/833/EU on the reuse of "
    "Commission documents; the source is acknowledged and the meaning is not distorted. Only the Official Journal "
    "edition is authentic."
)
POLISH_ACT_LICENCE = (
    "Polish normative acts are not subject to copyright (art. 4 pkt 1 ustawy z dnia 4 lutego 1994 r. o prawie "
    "autorskim i prawach pokrewnych, Dz.U. 2025 poz. 24). Source: Kancelaria Sejmu, ELI API."
)


@dataclass(frozen=True)
class Source:
    doc_id: str
    file: str
    title: str
    language: str
    version: str
    source_url: str
    licence: str
    request_headers: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True)
class SourceRecord:
    doc_id: str
    file: str
    title: str
    language: str
    version: str
    source_url: str
    retrieved_at: str
    sha256: str
    licence: str


SOURCES = (
    Source(
        doc_id="AMLR",
        file="amlr-2024-1624.en.xhtml",
        title=(
            "Regulation (EU) 2024/1624 of the European Parliament and of the Council of 31 May 2024 on the "
            "prevention of the use of the financial system for the purposes of money laundering or terrorist financing"
        ),
        language="en",
        version="OJ L, 2024/1624, 19.6.2024 (CELEX 32024R1624)",
        source_url="https://publications.europa.eu/resource/celex/32024R1624",
        licence=EUR_LEX_LICENCE,
        request_headers=(("Accept", "application/xhtml+xml, text/html"), ("Accept-Language", "eng")),
    ),
    Source(
        doc_id="PL-AML",
        file="pl-aml-2025-644.pdf",
        title="Ustawa z dnia 1 marca 2018 r. o przeciwdziałaniu praniu pieniędzy oraz finansowaniu terroryzmu",
        language="pl",
        version="Dz.U. 2025 poz. 644 (tekst jednolity, obwieszczenie Marszałka Sejmu z dnia 9 maja 2025 r.)",
        source_url="https://api.sejm.gov.pl/eli/acts/DU/2025/644/text/T/D20250644L.pdf",
        licence=POLISH_ACT_LICENCE,
    ),
)


class SourceIntegrityError(RuntimeError):
    pass


def fetch_sources(directory: Path) -> list[Path]:
    directory.mkdir(parents=True, exist_ok=True)
    records = {record.doc_id: record for record in read_manifest(directory)}
    for source in SOURCES:
        path = directory / source.file
        if not path.exists():
            path.write_bytes(download(source))
            records.setdefault(source.doc_id, new_record(source, path))
            write_manifest(directory, [records[source.doc_id] for source in SOURCES if source.doc_id in records])
    return [directory / record.file for record in verified_sources(directory)]


def verified_sources(directory: Path) -> list[SourceRecord]:
    records = {record.doc_id: record for record in read_manifest(directory)}
    missing = [source.doc_id for source in SOURCES if source.doc_id not in records]
    if missing:
        raise SourceIntegrityError(f"{directory / MANIFEST} has no entry for: {', '.join(missing)}")
    for record in records.values():
        actual = file_sha256(directory / record.file)
        if actual != record.sha256:
            raise SourceIntegrityError(
                f"{directory / record.file} has sha256 {actual}, manifest says {record.sha256}; "
                "delete the file and its manifest entry to refresh it deliberately"
            )
    return [records[source.doc_id] for source in SOURCES]


def download(source: Source) -> bytes:
    request = urllib.request.Request(source.source_url, headers=dict(source.request_headers))  # noqa: S310
    system_trust = truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    with urllib.request.urlopen(request, timeout=120, context=system_trust) as response:  # noqa: S310
        body: bytes = response.read()
    return body


def new_record(source: Source, path: Path) -> SourceRecord:
    return SourceRecord(
        doc_id=source.doc_id,
        file=source.file,
        title=source.title,
        language=source.language,
        version=source.version,
        source_url=source.source_url,
        retrieved_at=datetime.now(UTC).isoformat(timespec="seconds"),
        sha256=file_sha256(path),
        licence=source.licence,
    )


def file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read_manifest(directory: Path) -> list[SourceRecord]:
    path = directory / MANIFEST
    if not path.exists():
        return []
    return [SourceRecord(**entry) for entry in json.loads(path.read_text())]


def write_manifest(directory: Path, records: list[SourceRecord]) -> None:
    text = json.dumps([asdict(record) for record in records], indent=2, ensure_ascii=False)
    (directory / MANIFEST).write_text(text + "\n")
