from dataclasses import dataclass
from typing import Protocol

import psycopg
import tiktoken
from azure.identity import DefaultAzureCredential, get_bearer_token_provider
from langchain_openai import AzureOpenAIEmbeddings

from aml_investigator.core.sql import load
from aml_investigator.investigation.models.azure import API_VERSION, TOKEN_SCOPE

EMBEDDING_MODEL = "text-embedding-3-small"
BATCH_SIZE = 64
PASSAGES_TO_EMBED_SQL = load(__package__, "passages_to_embed")
SET_EMBEDDING_SQL = load(__package__, "set_embedding")


class DocumentEmbedder(Protocol):
    def embed_documents(self, texts: list[str]) -> list[list[float]]: ...


@dataclass(frozen=True)
class EmbedReport:
    passages: int
    tokens: int

    def __str__(self) -> str:
        return f"embedded {self.passages} passages, {self.tokens} tokens"


def azure_embeddings(endpoint: str) -> AzureOpenAIEmbeddings:
    return AzureOpenAIEmbeddings(
        azure_endpoint=endpoint,
        deployment=EMBEDDING_MODEL,
        model=EMBEDDING_MODEL,
        openai_api_version=API_VERSION,
        openai_api_key=None,
        azure_ad_token_provider=get_bearer_token_provider(DefaultAzureCredential(), TOKEN_SCOPE),
    )


def passage_input(heading: str | None, text: str) -> str:
    return text if heading is None else f"{heading}\n\n{text}"


def vector_literal(vector: list[float]) -> str:
    return "[" + ",".join(map(str, vector)) + "]"


def embed_missing(conn: psycopg.Connection[tuple[object, ...]], embeddings: DocumentEmbedder) -> EmbedReport:
    rows = conn.execute(PASSAGES_TO_EMBED_SQL).fetchall()
    inputs = [passage_input(str(heading) if heading else None, str(text)) for _, heading, text in rows]
    for start in range(0, len(rows), BATCH_SIZE):
        batch = slice(start, start + BATCH_SIZE)
        vectors = embeddings.embed_documents(inputs[batch])
        with conn.transaction(), conn.cursor() as cursor:
            cursor.executemany(
                SET_EMBEDDING_SQL,
                [
                    {"passage_id": row[0], "embedding": vector_literal(vector)}
                    for row, vector in zip(rows[batch], vectors, strict=True)
                ],
            )
    tokenizer = tiktoken.encoding_for_model(EMBEDDING_MODEL)
    return EmbedReport(len(rows), sum(len(tokenizer.encode(text)) for text in inputs))
