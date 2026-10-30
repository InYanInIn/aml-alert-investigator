from datetime import timedelta
from io import BytesIO
from typing import Any

import polars as pl
import psycopg

from aml_investigator.core.sql import load

RULE_ID = "R4_cycle"
MAX_HOPS = 4
SPAN_DAYS = 4
WINDOW_DAYS = SPAN_DAYS + 1
MIN_HOP_EUR = 100
FLOOR = MIN_HOP_EUR
MAX_EVIDENCE_TXNS = 50

SPAN = timedelta(days=SPAN_DAYS)
EDGE_SCHEMA = pl.Schema(
    {
        "txn_id": pl.Int64(),
        "from_account": pl.String(),
        "to_account": pl.String(),
        "ts": pl.Datetime("us"),
        "amount_paid_eur": pl.Float64(),
    }
)

DELETE_SQL = "DELETE FROM core.rule_candidates WHERE rule_id = %(rule_id)s"

EDGES_SQL = load(__package__, "r4_cycle_edges")

CANDIDATES_COPY = (
    "COPY core.rule_candidates (rule_id, account_id, alert_date, window_start, window_end, score, evidence_txn_ids) "
    "FROM STDIN"
)


def generate(conn: psycopg.Connection[Any]) -> int:
    with conn.transaction():
        conn.execute(DELETE_SQL, {"rule_id": RULE_ID})
        candidates = best_cycles_per_day(find_cycles(read_edges(conn)))
        write_candidates(conn, candidates)
        return candidates.height


def read_edges(conn: psycopg.Connection[Any]) -> pl.DataFrame:
    with conn.cursor().copy(EDGES_SQL, {"min_hop_eur": MIN_HOP_EUR}) as copy:
        csv = b"".join(copy)
    return pl.read_csv(BytesIO(csv), schema=EDGE_SCHEMA)


def find_cycles(edges: pl.DataFrame) -> pl.DataFrame:
    edges = edges_between_accounts_that_send_and_receive(edges)
    halves = {hops: half_cycles(edges, hops) for hops in range(1, (MAX_HOPS + 1) // 2 + 1)}
    return pl.concat(
        [close_cycles(halves[(hops + 1) // 2], halves[hops // 2]) for hops in range(2, MAX_HOPS + 1)]
    ).with_columns(pl.col("txn_ids").list.len().alias("hops"))


def edges_between_accounts_that_send_and_receive(edges: pl.DataFrame) -> pl.DataFrame:
    receivers = edges.select(pl.col("to_account").alias("from_account")).unique()
    senders = edges.select(pl.col("from_account").alias("to_account")).unique()
    kept = edges.join(receivers, on="from_account", how="semi").join(senders, on="to_account", how="semi")
    return kept if kept.height == edges.height else edges_between_accounts_that_send_and_receive(kept)


def half_cycles(edges: pl.DataFrame, hops: int) -> pl.DataFrame:
    paths = edges.select(
        pl.col("from_account").alias("start"),
        pl.col("to_account").alias("end"),
        pl.lit([], dtype=pl.List(pl.String)).alias("via"),
        pl.col("ts").alias("first_ts"),
        pl.col("ts").alias("last_ts"),
        pl.col("amount_paid_eur").alias("weakest_eur"),
        pl.concat_list("txn_id").alias("txn_ids"),
    )
    for _ in range(hops - 1):
        paths = (
            paths.join(edges, left_on="end", right_on="from_account")
            .filter(
                pl.col("ts") > pl.col("last_ts"),
                pl.col("ts") - pl.col("first_ts") <= SPAN,
                pl.col("to_account") != pl.col("start"),
                ~pl.col("via").list.contains(pl.col("to_account")),
            )
            .select(
                "start",
                pl.col("to_account").alias("end"),
                pl.concat_list("via", "end").alias("via"),
                "first_ts",
                pl.col("ts").alias("last_ts"),
                pl.min_horizontal("weakest_eur", "amount_paid_eur").alias("weakest_eur"),
                pl.concat_list("txn_ids", "txn_id").alias("txn_ids"),
            )
        )
    return paths


def close_cycles(outbound: pl.DataFrame, inbound: pl.DataFrame) -> pl.DataFrame:
    return (
        outbound.join(inbound, left_on=["end", "start"], right_on=["start", "end"], suffix="_inbound")
        .filter(
            pl.col("first_ts_inbound") > pl.col("last_ts"),
            pl.col("last_ts_inbound") - pl.col("first_ts") <= SPAN,
            pl.col("via").list.set_intersection("via_inbound").list.len() == 0,
        )
        .select(
            pl.col("start").alias("account_id"),
            "first_ts",
            pl.col("last_ts_inbound").alias("closing_ts"),
            pl.min_horizontal("weakest_eur", "weakest_eur_inbound").alias("weakest_eur"),
            pl.concat_list("txn_ids", "txn_ids_inbound").alias("txn_ids"),
        )
    )


def best_cycles_per_day(cycles: pl.DataFrame) -> pl.DataFrame:
    day = ["account_id", "alert_date"]
    return (
        cycles.with_columns(pl.col("closing_ts").dt.date().alias("alert_date"))
        .filter(pl.col("weakest_eur") == pl.col("weakest_eur").max().over(day))
        .sort(*day, "txn_ids")
        .filter(pl.col("hops").cum_sum().over(day) <= MAX_EVIDENCE_TXNS)
        .group_by(day)
        .agg(
            pl.col("first_ts").min().dt.truncate("1d").alias("window_start"),
            pl.col("weakest_eur").first().round(2).alias("score"),
            pl.col("txn_ids")
            .list.explode(keep_nulls=False, empty_as_null=False)
            .unique()
            .sort()
            .alias("evidence_txn_ids"),
        )
        .with_columns((pl.col("alert_date").cast(pl.Datetime("us")) + timedelta(days=1)).alias("window_end"))
        .sort(day)
    )


def write_candidates(conn: psycopg.Connection[Any], candidates: pl.DataFrame) -> None:
    rows = candidates.select("account_id", "alert_date", "window_start", "window_end", "score", "evidence_txn_ids")
    with conn.cursor().copy(CANDIDATES_COPY) as copy:
        for row in rows.iter_rows():
            copy.write_row((RULE_ID, *row))
