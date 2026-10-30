from datetime import date, datetime
from typing import Any, Literal

import psycopg
from fastmcp import FastMCP
from fastmcp.exceptions import ToolError
from psycopg.rows import class_row, dict_row
from pydantic import BaseModel

from aml_investigator.core.sql import load

MAX_TRANSACTIONS = 50
LIST_TRANSACTIONS_SQL = load(__package__, "list_transactions")


class RuleHit(BaseModel):
    rule_id: str
    score: float
    evidence_txn_ids: list[int]


class Alert(BaseModel):
    alert_id: int
    account_id: str
    alert_date: date
    window_start: datetime
    window_end: datetime
    as_of: datetime
    rule_hits: list[RuleHit]


class Transaction(BaseModel):
    txn_id: int
    ts: datetime
    from_account: str
    to_account: str
    amount_paid: float
    paid_currency: str
    amount_received: float
    received_currency: str
    payment_format: str


class TransactionPage(BaseModel):
    as_of: datetime
    truncated: bool
    transactions: list[Transaction]


def read_only_connection(dsn: str) -> psycopg.Connection[tuple[object, ...]]:
    conn = psycopg.connect(dsn)
    conn.read_only = True
    return conn


def load_alert(conn: psycopg.Connection[tuple[object, ...]], alert_id: int) -> Alert:
    with conn.cursor(row_factory=dict_row) as cursor:
        cursor.execute(
            "SELECT alert_id, account_id, alert_date, window_start, window_end, window_end AS as_of "
            "FROM core.alerts WHERE alert_id = %s",
            (alert_id,),
        )
        alert = cursor.fetchone()
    if alert is None:
        raise ToolError(f"Alert {alert_id} does not exist.")
    with conn.cursor(row_factory=class_row(RuleHit)) as cursor:
        cursor.execute(
            "SELECT rule_id, score, evidence_txn_ids FROM core.alert_rule_hits WHERE alert_id = %s ORDER BY rule_id",
            (alert_id,),
        )
        return Alert(**alert, rule_hits=cursor.fetchall())


def parse_timestamp(name: str, value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        raise ToolError(f"{name} must be an ISO-8601 timestamp such as 2022-09-01T00:00:00, got {value!r}.") from None
    if parsed.tzinfo is not None:
        raise ToolError(f"{name} must not carry a timezone offset, got {value!r}.")
    return parsed


def build_server(dsn: str) -> FastMCP[Any]:
    server: FastMCP[Any] = FastMCP("aml-investigator")

    @server.tool
    def get_alert(alert_id: int) -> Alert:
        """Return one AML monitoring alert: the alerted account, the alert date, the observation window and the
        rule hits that raised it. Each rule hit has a rule_id, a score and the evidence_txn_ids of the transactions
        that triggered it, ordered by rule_id. as_of equals window_end: it is the point in time of the alert, and no
        tool shows any transaction at or after it. Timestamps are ISO-8601 without timezone."""
        with read_only_connection(dsn) as conn:
            return load_alert(conn, alert_id)

    @server.tool
    def list_transactions(
        alert_id: int,
        account_id: str,
        direction: Literal["in", "out", "both"] = "both",
        start: str | None = None,
        end: str | None = None,
        min_amount: float = 0.0,
        limit: int = MAX_TRANSACTIONS,
    ) -> TransactionPage:
        """List transactions of one account, oldest first (ordered by ts, then txn_id). direction selects
        outgoing ("out", the account is from_account), incoming ("in", the account is to_account) or both.
        start and end are ISO-8601 timestamps without timezone and select start <= ts < end; start defaults to the
        alert's window_start. The result never contains transactions at or after the alert's as_of (its
        window_end): end defaults to as_of and any later end is clamped to it, so the result reflects only what
        was known when the alert was raised. min_amount keeps transactions whose amount_paid is at least that
        value. At most 50 rows are returned (limit is clamped to 1..50); truncated is true when more
        transactions matched, in which case narrow start/end or raise min_amount."""
        capped = min(max(limit, 1), MAX_TRANSACTIONS)
        with read_only_connection(dsn) as conn:
            alert = load_alert(conn, alert_id)
            lower = parse_timestamp("start", start) if start is not None else alert.window_start
            upper = min(parse_timestamp("end", end), alert.as_of) if end is not None else alert.as_of
            with conn.cursor(row_factory=class_row(Transaction)) as cursor:
                cursor.execute(
                    LIST_TRANSACTIONS_SQL,
                    {
                        "lower": lower,
                        "upper": upper,
                        "min_amount": min_amount,
                        "outgoing": direction in ("out", "both"),
                        "incoming": direction in ("in", "both"),
                        "account": account_id,
                        "fetch": capped + 1,
                    },
                )
                rows = cursor.fetchall()
        return TransactionPage(as_of=alert.as_of, truncated=len(rows) > capped, transactions=rows[:capped])

    return server
