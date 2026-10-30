import csv
import hashlib
import re
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

import polars as pl
import psycopg
from psycopg import sql

from aml_investigator.data.fx import eur_per_unit, pair_rates, round_trip_error, used_currencies

SALT = "aml-investigator-v1"
RAW_COLUMNS = [
    "timestamp",
    "from_bank",
    "from_account",
    "to_bank",
    "to_account",
    "amount_received",
    "receiving_currency",
    "amount_paid",
    "payment_currency",
    "payment_format",
    "is_laundering",
]
CONTENT_COLUMNS = [column for column in RAW_COLUMNS if column != "is_laundering"]
ACCOUNT_KEY = ["bank_id", "account_number"]
PATTERN_HEADER = re.compile(r"^BEGIN LAUNDERING ATTEMPT - ([A-Z-]+)(?::.*)?$")
PATTERN_FOOTER = "END LAUNDERING ATTEMPT"
COPY_BATCH_ROWS = 200_000


@dataclass(frozen=True)
class IngestReport:
    transactions: int
    accounts: int
    laundering: int
    pattern_rows: int
    dropped_tail: int
    currencies: int
    fx_round_trip_error: float

    def __str__(self) -> str:
        return (
            f"transactions: {self.transactions}, accounts: {self.accounts}, laundering: {self.laundering}, "
            f"pattern rows: {self.pattern_rows}, dropped tail: {self.dropped_tail}, currencies: {self.currencies}, "
            f"fx round-trip error: {self.fx_round_trip_error:.6f}"
        )


def ingest(
    conn: psycopg.Connection[Any], raw_dir: Path, prefix: str, *, period_end: date, limit: int | None
) -> IngestReport:
    raw = read_raw_transactions(raw_dir / f"{prefix}_Trans.csv")
    in_period = raw.filter(pl.col("ts").dt.date() <= period_end)
    numbered = number_transactions(in_period)
    loaded = numbered if limit is None else numbered.filter(pl.col("txn_id") <= limit)
    accounts = referenced_accounts(read_raw_accounts(raw_dir / f"{prefix}_accounts.csv"), loaded)
    attempts = match_pattern_rows(read_pattern_rows(raw_dir / f"{prefix}_Patterns.txt"), raw, loaded)
    rates = pair_rates(in_period)
    fx_rates = eur_per_unit(rates, used_currencies(loaded))
    with conn.transaction():
        refuse_non_empty(conn)
        copy_frame(
            conn, "core", "accounts", accounts.select("account_id", "bank_id", "bank_name", "entity_id", "entity_type")
        )
        copy_frame(conn, "core", "fx_rates", fx_rates)
        copy_frame(conn, "core", "transactions", core_transactions(loaded, accounts, fx_rates))
        copy_frame(
            conn,
            "truth",
            "transaction_labels",
            loaded.select("txn_id", (pl.col("is_laundering") == "1").alias("is_laundering")),
        )
        copy_frame(conn, "truth", "pattern_attempts", attempts)
    return IngestReport(
        transactions=loaded.height,
        accounts=accounts.height,
        laundering=loaded.filter(pl.col("is_laundering") == "1").height,
        pattern_rows=attempts.height,
        dropped_tail=raw.height - in_period.height,
        currencies=fx_rates.height,
        fx_round_trip_error=round_trip_error(rates),
    )


def read_raw_transactions(path: Path) -> pl.DataFrame:
    return pl.read_csv(
        path,
        new_columns=RAW_COLUMNS,
        schema_overrides=dict.fromkeys(RAW_COLUMNS, pl.String),
        empty_string_is_null=False,
    ).with_columns(
        pl.col("timestamp").str.strptime(pl.Datetime, "%Y/%m/%d %H:%M").alias("ts"),
        pl.col("from_bank").cast(pl.Int32).alias("from_bank_id"),
        pl.col("to_bank").cast(pl.Int32).alias("to_bank_id"),
    )


def number_transactions(transactions: pl.DataFrame) -> pl.DataFrame:
    content_hash = pl.concat_str(CONTENT_COLUMNS, separator=",").map_batches(sha256_hex, return_dtype=pl.String)
    return (
        transactions.with_columns(content_hash.alias("content_sha256"))
        .sort("ts", "content_sha256", maintain_order=True)
        .with_row_index("txn_id", offset=1)
        .drop("content_sha256")
    )


def sha256_hex(values: pl.Series) -> pl.Series:
    return pl.Series([hashlib.sha256(value.encode()).hexdigest() for value in values], dtype=pl.String)


def pseudonym(prefix: str, *columns: str) -> pl.Expr:
    salted = pl.concat_str([pl.lit(SALT), *columns], separator="|")
    return pl.lit(prefix) + salted.map_batches(sha256_hex, return_dtype=pl.String).str.slice(0, 12)


def read_raw_accounts(path: Path) -> pl.DataFrame:
    return pl.read_csv(path, infer_schema=False).select(
        pl.col("Bank ID").cast(pl.Int32).alias("bank_id"),
        pl.col("Bank Name").alias("bank_name"),
        pl.col("Account Number").alias("account_number"),
        pl.col("Entity ID").alias("source_entity_id"),
        pl.col("Entity Name").str.replace(r"\s*#\d+$", "").alias("entity_type"),
    )


def referenced_accounts(accounts: pl.DataFrame, transactions: pl.DataFrame) -> pl.DataFrame:
    referenced = pl.concat(
        [
            transactions.select(
                pl.col(f"{side}_bank_id").alias("bank_id"), pl.col(f"{side}_account").alias("account_number")
            )
            for side in ("from", "to")
        ]
    ).unique()
    used = accounts.join(referenced, on=ACCOUNT_KEY, how="semi")
    if used.height != referenced.height or used.select(ACCOUNT_KEY).n_unique() != used.height:
        raise ValueError(
            f"accounts file must list each referenced account exactly once: {used.height} rows for "
            f"{referenced.height} referenced accounts"
        )
    pseudonymised = used.with_columns(
        pseudonym("A", *ACCOUNT_KEY).alias("account_id"), pseudonym("E", "source_entity_id").alias("entity_id")
    )
    require_injective(pseudonymised, "account_id", ACCOUNT_KEY)
    require_injective(pseudonymised, "entity_id", ["source_entity_id"])
    return pseudonymised.select("account_id", *ACCOUNT_KEY, "bank_name", "entity_id", "entity_type")


def require_injective(frame: pl.DataFrame, pseudonym_column: str, source_columns: list[str]) -> None:
    pairs = frame.select(pseudonym_column, *source_columns).unique()
    if pairs[pseudonym_column].n_unique() != pairs.height:
        raise ValueError(f"{pseudonym_column} pseudonym collision: two source keys map to one pseudonym")


def core_transactions(transactions: pl.DataFrame, accounts: pl.DataFrame, fx_rates: pl.DataFrame) -> pl.DataFrame:
    transactions = transactions.join(fx_rates, left_on="payment_currency", right_on="currency")
    keyed = accounts.select("account_id", *ACCOUNT_KEY)
    for side in ("from", "to"):
        transactions = transactions.join(
            keyed.rename({"account_id": f"{side}_account_id"}),
            left_on=[f"{side}_bank_id", f"{side}_account"],
            right_on=ACCOUNT_KEY,
        )
    return transactions.select(
        "txn_id",
        "ts",
        pl.col("from_account_id").alias("from_account"),
        pl.col("to_account_id").alias("to_account"),
        "amount_paid",
        pl.col("payment_currency").alias("paid_currency"),
        "amount_received",
        pl.col("receiving_currency").alias("received_currency"),
        "payment_format",
        (pl.col("amount_paid").cast(pl.Float64) * pl.col("eur_per_unit")).alias("amount_paid_eur"),
    ).sort("txn_id")


def read_pattern_rows(path: Path) -> pl.DataFrame:
    rows: list[list[str | int]] = []
    attempt_id = 0
    typology = ""
    with path.open(newline="") as handle:
        for line in (raw_line.strip() for raw_line in handle):
            header = PATTERN_HEADER.match(line)
            if header:
                attempt_id += 1
                typology = header.group(1).lower().replace("-", "_")
            elif line and not line.startswith(PATTERN_FOOTER):
                rows.append([attempt_id, typology, *next(csv.reader([line]))])
    return pl.DataFrame(
        rows,
        schema={"attempt_id": pl.Int32, "typology": pl.String} | dict.fromkeys(RAW_COLUMNS, pl.String),
        orient="row",
    )


def match_pattern_rows(patterns: pl.DataFrame, raw: pl.DataFrame, loaded: pl.DataFrame) -> pl.DataFrame:
    matches = patterns.join(raw.select(RAW_COLUMNS).with_row_index("raw_row"), on=RAW_COLUMNS, how="left")
    if matches.height != patterns.height or matches["raw_row"].null_count():
        raise ValueError("every pattern row must match exactly one transaction row")
    return patterns.join(loaded.select("txn_id", *RAW_COLUMNS), on=RAW_COLUMNS).select(
        "attempt_id", "typology", "txn_id"
    )


def refuse_non_empty(conn: psycopg.Connection[Any]) -> None:
    if conn.execute("SELECT EXISTS (SELECT FROM core.transactions)").fetchone() == (True,):
        raise RuntimeError("core.transactions is not empty; ingest loads into an empty database only")


def copy_frame(conn: psycopg.Connection[Any], schema: str, table: str, frame: pl.DataFrame) -> None:
    statement = sql.SQL("COPY {} ({}) FROM STDIN (FORMAT csv)").format(
        sql.Identifier(schema, table), sql.SQL(", ").join(map(sql.Identifier, frame.columns))
    )
    with conn.cursor().copy(statement) as copy:
        for chunk in csv_chunks(frame):
            copy.write(chunk)


def csv_chunks(frame: pl.DataFrame) -> Iterable[str]:
    for batch in frame.iter_slices(COPY_BATCH_ROWS):
        yield batch.write_csv(include_header=False, datetime_format="%Y-%m-%d %H:%M:%S")
