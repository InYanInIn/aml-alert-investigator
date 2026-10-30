import json
import logging
import sys
import time
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import UTC, datetime
from types import MappingProxyType
from typing import TextIO

CONTEXT: ContextVar[Mapping[str, object]] = ContextVar("log_context", default=MappingProxyType({}))


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        line: dict[str, object] = {
            "ts": datetime.fromtimestamp(record.created, UTC).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            **CONTEXT.get(),
            **getattr(record, "fields", {}),
        }
        if record.exc_info:
            line["exception"] = self.formatException(record.exc_info)
        return json.dumps(line, default=str)


class JsonHandler(logging.StreamHandler[TextIO]):
    def __init__(self) -> None:
        super().__init__(sys.stderr)
        self.setFormatter(JsonFormatter())


def configure_logging(level: str = "INFO") -> None:
    root = logging.getLogger()
    for handler in [handler for handler in root.handlers if isinstance(handler, JsonHandler)]:
        root.removeHandler(handler)
    root.addHandler(JsonHandler())
    root.setLevel(level)


@contextmanager
def log_context(**fields: object) -> Iterator[None]:
    token = CONTEXT.set({**CONTEXT.get(), **fields})
    try:
        yield
    finally:
        CONTEXT.reset(token)


@contextmanager
def timed_event(logger: logging.Logger, event: str, **fields: object) -> Iterator[dict[str, object]]:
    outcome: dict[str, object] = {}
    started = time.perf_counter()
    try:
        yield outcome
    except Exception as error:
        logger.warning(event, extra={"fields": {**fields, **outcome, **elapsed(started), "error": repr(error)}})
        raise
    logger.info(event, extra={"fields": {**fields, **outcome, **elapsed(started)}})


def elapsed(started: float) -> dict[str, object]:
    return {"duration_ms": round((time.perf_counter() - started) * 1000, 1)}
