# /// script
# requires-python = ">=3.12"
# dependencies = ["polars==1.*"]
# ///
import csv
import re
import sys
from datetime import date
from pathlib import Path

import polars as pl

TRANS_COLUMNS = [
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
PRIMARY_PERIOD_END = date(2022, 9, 10)
PATTERN_HEADER = re.compile(r"^BEGIN LAUNDERING ATTEMPT - ([A-Z-]+)(?::\s*(.*))?$")
ENTITY_NUMBER = r"\s*#\d+$"


def read_transactions(path: Path) -> pl.DataFrame:
    return (
        pl.read_csv(path, new_columns=TRANS_COLUMNS, schema_overrides=dict.fromkeys(TRANS_COLUMNS, pl.String))
        .with_row_index("file_row")
        .with_columns(
            pl.col("timestamp").str.strptime(pl.Datetime, "%Y/%m/%d %H:%M"),
            pl.col("is_laundering").cast(pl.Int8),
            pl.col("amount_paid").cast(pl.Float64),
            pl.col("amount_received").cast(pl.Float64),
        )
    )


def read_patterns(path: Path) -> pl.DataFrame:
    rows: list[dict[str, str | int]] = []
    attempt_id = 0
    typology = ""
    with path.open(newline="") as handle:
        for line in handle:
            line = line.strip()
            header = PATTERN_HEADER.match(line)
            if header:
                attempt_id += 1
                typology = header.group(1)
            elif line and not line.startswith("END LAUNDERING ATTEMPT"):
                values = next(csv.reader([line]))
                rows.append(
                    {"attempt_id": attempt_id, "typology": typology, **dict(zip(TRANS_COLUMNS, values, strict=True))}
                )
    return pl.DataFrame(rows).with_columns(
        pl.col("timestamp").str.strptime(pl.Datetime, "%Y/%m/%d %H:%M"),
        pl.col("amount_paid").cast(pl.Float64),
        pl.col("amount_received").cast(pl.Float64),
        pl.col("is_laundering").cast(pl.Int8),
    )


def section(title: str) -> None:
    print(f"\n### {title}\n")


def show(frame: pl.DataFrame) -> None:
    with pl.Config(tbl_rows=40, tbl_cols=20, tbl_width_chars=200, fmt_str_lengths=40):
        print(frame)


def count(frame: pl.DataFrame, predicate: pl.Expr) -> int:
    return frame.filter(predicate).height


def after_primary_period() -> pl.Expr:
    return pl.col("timestamp").dt.date() > PRIMARY_PERIOD_END


def profile_transactions(trans: pl.DataFrame) -> None:
    section("Transactions")
    laundering = count(trans, pl.col("is_laundering") == 1)
    duplicates = trans.height - trans.drop("file_row").unique().height
    self_transfers = count(
        trans, (pl.col("from_bank") == pl.col("to_bank")) & (pl.col("from_account") == pl.col("to_account"))
    )
    cross_currency = count(trans, pl.col("receiving_currency") != pl.col("payment_currency"))
    print(f"rows: {trans.height}")
    print(f"laundering rows: {laundering} (1 per {trans.height / max(laundering, 1):.0f})")
    print(f"timestamp range: {trans['timestamp'].min()} .. {trans['timestamp'].max()}")
    print(f"exact duplicate rows: {duplicates}")
    print(f"self-transfers (same bank and account): {self_transfers}")
    print(f"cross-currency rows: {cross_currency}")
    show(
        trans.group_by(pl.col("timestamp").dt.date().alias("day"))
        .agg(pl.len().alias("rows"), pl.col("is_laundering").sum().alias("laundering"))
        .sort("day")
    )

    section("Tail after the primary period")
    tail = trans.filter(after_primary_period())
    print(f"rows after {PRIMARY_PERIOD_END}: {tail.height}, of which laundering: {tail['is_laundering'].sum()}")

    section("Payment format")
    show(
        trans.group_by("payment_format")
        .agg(pl.len().alias("rows"), pl.col("is_laundering").sum().alias("laundering"))
        .with_columns((pl.col("laundering") / pl.col("rows")).alias("laundering_rate"))
        .sort("rows", descending=True)
    )

    section("File order vs label (decile of file row)")
    show(
        trans.with_columns((pl.col("file_row") * 10 // trans.height).alias("decile"))
        .group_by("decile")
        .agg(pl.len().alias("rows"), pl.col("is_laundering").mean().alias("laundering_rate"))
        .sort("decile")
    )

    section("Is the file sorted by timestamp?")
    out_of_order = count(trans, pl.col("timestamp") < pl.col("timestamp").shift(1))
    print(f"rows whose timestamp is earlier than the previous row: {out_of_order}")


def account_involvement(trans: pl.DataFrame) -> pl.DataFrame:
    sides = [
        trans.select(pl.col("from_bank").alias("bank"), pl.col("from_account").alias("account"), "is_laundering"),
        trans.select(pl.col("to_bank").alias("bank"), pl.col("to_account").alias("account"), "is_laundering"),
    ]
    return pl.concat(sides).group_by("bank", "account").agg(pl.col("is_laundering").max().alias("involved"))


def with_entity_type(frame: pl.DataFrame) -> pl.DataFrame:
    return frame.with_columns(pl.col("Entity Name").str.replace(ENTITY_NUMBER, "").alias("entity_type"))


def profile_accounts(trans: pl.DataFrame, accounts: pl.DataFrame) -> None:
    section("Accounts file")
    account_numbers = accounts["Account Number"].n_unique()
    entities = accounts["Entity ID"].n_unique()
    print(f"rows: {accounts.height}, distinct account numbers: {account_numbers}, distinct entities: {entities}")
    show(with_entity_type(accounts).group_by("entity_type").agg(pl.len().alias("accounts")).sort("accounts"))

    per_entity = accounts.group_by("Entity ID").agg(pl.len().alias("accounts"))
    shared_entities = count(per_entity, pl.col("accounts") > 1)
    print(f"accounts per entity: max {per_entity['accounts'].max()}, entities with >1 account: {shared_entities}")

    involvement = account_involvement(trans)
    print(f"distinct (bank, account) in transactions: {involvement.height}")
    print(f"distinct account numbers in transactions: {involvement['account'].n_unique()}")
    joined = involvement.join(
        accounts.select(pl.col("Account Number").alias("account"), pl.col("Bank ID").alias("bank_id"), "Entity Name"),
        on="account",
        how="left",
    )
    as_integer = pl.col("bank").cast(pl.Int64, strict=False) == pl.col("bank_id").cast(pl.Int64, strict=False)
    print(f"transaction accounts missing from accounts file: {count(joined, pl.col('Entity Name').is_null())}")
    print(f"bank id equal as string: {count(joined, pl.col('bank') == pl.col('bank_id'))}")
    print(f"bank id equal as integer: {count(joined, as_integer)}")

    section("Account involvement by entity type")
    show(
        with_entity_type(joined)
        .group_by("entity_type")
        .agg(pl.len().alias("accounts"), pl.col("involved").mean().alias("involved_rate"))
        .sort("accounts", descending=True)
    )

    section("Account number order vs involvement (decile of hex value)")
    show(
        involvement.with_columns(pl.col("account").str.to_integer(base=16, strict=False).alias("account_int"))
        .with_columns((pl.col("account_int").rank("ordinal") * 10 // (involvement.height + 1)).alias("decile"))
        .group_by("decile")
        .agg(pl.len().alias("accounts"), pl.col("involved").mean().alias("involved_rate"))
        .sort("decile")
    )


def profile_patterns(trans: pl.DataFrame, patterns: pl.DataFrame) -> None:
    section("Patterns file")
    attempts = patterns["attempt_id"].n_unique()
    labelled_zero = count(patterns, pl.col("is_laundering") == 0)
    print(f"attempts: {attempts}, rows: {patterns.height}, rows labelled 0: {labelled_zero}")
    show(
        patterns.group_by("typology")
        .agg(pl.col("attempt_id").n_unique().alias("attempts"), pl.len().alias("rows"))
        .sort("typology")
    )

    keys = [c for c in TRANS_COLUMNS if c != "is_laundering"]
    matched = patterns.join(trans.select(*keys, "file_row"), on=keys, how="left")
    found = matched.filter(pl.col("file_row").is_not_null())
    ambiguous = count(matched.group_by(["attempt_id", *keys]).len(), pl.col("len") > 1)
    laundering = count(trans, pl.col("is_laundering") == 1)
    covered = found["file_row"].n_unique()
    print(f"pattern rows matching a transaction exactly: {found.height} of {patterns.height}")
    print(f"pattern rows matching more than one transaction: {ambiguous}")
    print(
        f"laundering transactions covered by patterns: {covered} of {laundering} ({covered / max(laundering, 1):.1%})"
    )
    print(f"pattern rows after {PRIMARY_PERIOD_END}: {count(patterns, after_primary_period())}")


def main(raw_dir: Path, prefix: str) -> None:
    print(f"## {prefix}")
    trans = read_transactions(raw_dir / f"{prefix}_Trans.csv")
    accounts = pl.read_csv(raw_dir / f"{prefix}_accounts.csv", infer_schema=False)
    patterns = read_patterns(raw_dir / f"{prefix}_Patterns.txt")
    profile_transactions(trans)
    profile_accounts(trans, accounts)
    profile_patterns(trans, patterns)


if __name__ == "__main__":
    main(Path(sys.argv[1]), sys.argv[2])
