import json
from datetime import datetime
from typing import Any

import pytest
from fastmcp import Client
from fastmcp.exceptions import ToolError

from aml_investigator.mcp_server import build_server
from tests.conftest import Database

WINDOW_START = datetime(2022, 9, 1)
AS_OF = datetime(2022, 9, 3)


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def add_transaction(
    db: Database, txn_id: int, ts: datetime, sender: str, receiver: str, amount: float = 100.0, fmt: str = "ACH"
) -> None:
    with db.owner() as conn:
        conn.execute(
            "INSERT INTO core.transactions VALUES (%s, %s, %s, %s, %s, 'Euro', %s, 'Euro', %s, %s)",
            (txn_id, ts, sender, receiver, amount, amount, fmt, amount),
        )


@pytest.fixture
def alert_id(db: Database) -> int:
    with db.owner() as conn:
        for account in ("A1", "A2", "A3"):
            conn.execute("INSERT INTO core.accounts VALUES (%s, 1, 'Bank', %s, 'Corporation')", (account, account))
        row = conn.execute(
            "INSERT INTO core.alerts (account_id, alert_date, window_start, window_end) "
            "VALUES ('A1', '2022-09-02', %s, %s) RETURNING alert_id",
            (WINDOW_START, AS_OF),
        ).fetchone()
        assert row is not None
        created = int(str(row[0]))
        conn.execute("INSERT INTO core.alert_rule_hits VALUES (%s, 'R2_fan_out', 6.0, ARRAY[11, 12])", (created,))
        conn.execute("INSERT INTO core.alert_rule_hits VALUES (%s, 'R1_volume', 2.5, ARRAY[11])", (created,))
    return created


async def call(db: Database, tool: str, **arguments: Any) -> dict[str, Any]:
    async with Client(build_server(db.mcp_dsn)) as client:
        result = await client.call_tool(tool, arguments)
    content: dict[str, Any] | None = result.structured_content
    assert content is not None
    return content


async def transaction_ids(db: Database, **arguments: Any) -> list[int]:
    page = await call(db, "list_transactions", **arguments)
    return [transaction["txn_id"] for transaction in page["transactions"]]


@pytest.mark.anyio
async def test_get_alert_shape_and_rule_hit_order(db: Database, alert_id: int) -> None:
    alert = await call(db, "get_alert", alert_id=alert_id)

    assert alert == {
        "alert_id": alert_id,
        "account_id": "A1",
        "alert_date": "2022-09-02",
        "window_start": "2022-09-01T00:00:00",
        "window_end": "2022-09-03T00:00:00",
        "as_of": "2022-09-03T00:00:00",
        "rule_hits": [
            {"rule_id": "R1_volume", "score": 2.5, "evidence_txn_ids": [11]},
            {"rule_id": "R2_fan_out", "score": 6.0, "evidence_txn_ids": [11, 12]},
        ],
    }


@pytest.mark.anyio
async def test_unknown_alert_is_a_tool_error(db: Database, alert_id: int) -> None:
    unknown = alert_id + 1
    async with Client(build_server(db.mcp_dsn)) as client:
        with pytest.raises(ToolError, match=f"Alert {unknown} does not exist"):
            await client.call_tool("get_alert", {"alert_id": unknown})
        with pytest.raises(ToolError, match=f"Alert {unknown} does not exist"):
            await client.call_tool("list_transactions", {"alert_id": unknown, "account_id": "A1"})


@pytest.mark.anyio
async def test_transaction_shape(db: Database, alert_id: int) -> None:
    add_transaction(db, 11, datetime(2022, 9, 2, 10, 15), "A1", "A2", 2848.96, "Wire")

    page = await call(db, "list_transactions", alert_id=alert_id, account_id="A1")

    assert page == {
        "as_of": "2022-09-03T00:00:00",
        "truncated": False,
        "transactions": [
            {
                "txn_id": 11,
                "ts": "2022-09-02T10:15:00",
                "from_account": "A1",
                "to_account": "A2",
                "amount_paid": 2848.96,
                "paid_currency": "Euro",
                "amount_received": 2848.96,
                "received_currency": "Euro",
                "payment_format": "Wire",
            }
        ],
    }


@pytest.mark.anyio
async def test_end_is_clamped_to_as_of(db: Database, alert_id: int) -> None:
    add_transaction(db, 1, datetime(2022, 9, 2, 23, 59, 59), "A1", "A2")
    add_transaction(db, 2, AS_OF, "A1", "A2")
    add_transaction(db, 3, datetime(2022, 9, 4), "A1", "A2")

    assert await transaction_ids(db, alert_id=alert_id, account_id="A1") == [1]
    assert await transaction_ids(db, alert_id=alert_id, account_id="A1", end="2022-12-31T00:00:00") == [1]


@pytest.mark.anyio
async def test_start_defaults_to_window_start_and_can_look_earlier(db: Database, alert_id: int) -> None:
    add_transaction(db, 1, datetime(2022, 8, 31, 23, 59), "A1", "A2")
    add_transaction(db, 2, WINDOW_START, "A1", "A2")

    assert await transaction_ids(db, alert_id=alert_id, account_id="A1") == [2]
    assert await transaction_ids(db, alert_id=alert_id, account_id="A1", start="2022-08-01") == [1, 2]


@pytest.mark.anyio
async def test_end_is_exclusive(db: Database, alert_id: int) -> None:
    add_transaction(db, 1, datetime(2022, 9, 2), "A1", "A2")

    assert await transaction_ids(db, alert_id=alert_id, account_id="A1", end="2022-09-02T00:00:00") == []
    assert await transaction_ids(db, alert_id=alert_id, account_id="A1", end="2022-09-02T00:00:01") == [1]


@pytest.mark.anyio
async def test_direction_filters(db: Database, alert_id: int) -> None:
    add_transaction(db, 1, datetime(2022, 9, 1, 1), "A1", "A2")
    add_transaction(db, 2, datetime(2022, 9, 1, 2), "A2", "A1")
    add_transaction(db, 3, datetime(2022, 9, 1, 3), "A2", "A3")

    assert await transaction_ids(db, alert_id=alert_id, account_id="A1", direction="out") == [1]
    assert await transaction_ids(db, alert_id=alert_id, account_id="A1", direction="in") == [2]
    assert await transaction_ids(db, alert_id=alert_id, account_id="A1", direction="both") == [1, 2]
    assert await transaction_ids(db, alert_id=alert_id, account_id="A1") == [1, 2]


@pytest.mark.anyio
async def test_min_amount_filters_on_amount_paid(db: Database, alert_id: int) -> None:
    add_transaction(db, 1, datetime(2022, 9, 1, 1), "A1", "A2", 99.99)
    add_transaction(db, 2, datetime(2022, 9, 1, 2), "A1", "A2", 100.0)
    add_transaction(db, 3, datetime(2022, 9, 1, 3), "A1", "A2", 5000.0)

    assert await transaction_ids(db, alert_id=alert_id, account_id="A1", min_amount=100.0) == [2, 3]
    assert await transaction_ids(db, alert_id=alert_id, account_id="A1", min_amount=100.01) == [3]


@pytest.mark.anyio
async def test_limit_is_clamped_and_truncation_flagged(db: Database, alert_id: int) -> None:
    for txn_id in range(1, 56):
        add_transaction(db, txn_id, datetime(2022, 9, 1, 0, txn_id), "A1", "A2")

    default_page = await call(db, "list_transactions", alert_id=alert_id, account_id="A1")
    small_page = await call(db, "list_transactions", alert_id=alert_id, account_id="A1", limit=3)
    oversized_page = await call(db, "list_transactions", alert_id=alert_id, account_id="A1", limit=500)
    zero_page = await call(db, "list_transactions", alert_id=alert_id, account_id="A1", limit=0)
    exact_page = await call(
        db, "list_transactions", alert_id=alert_id, account_id="A1", start="2022-09-01T00:06:00", limit=50
    )

    assert len(default_page["transactions"]) == 50
    assert default_page["truncated"] is True
    assert [t["txn_id"] for t in small_page["transactions"]] == [1, 2, 3]
    assert small_page["truncated"] is True
    assert len(oversized_page["transactions"]) == 50
    assert [t["txn_id"] for t in zero_page["transactions"]] == [1]
    assert zero_page["truncated"] is True
    assert len(exact_page["transactions"]) == 50
    assert exact_page["truncated"] is False


@pytest.mark.anyio
async def test_ordering_is_by_ts_then_txn_id(db: Database, alert_id: int) -> None:
    add_transaction(db, 30, datetime(2022, 9, 1, 5), "A1", "A2")
    add_transaction(db, 20, datetime(2022, 9, 1, 6), "A1", "A2")
    add_transaction(db, 10, datetime(2022, 9, 1, 6), "A2", "A1")
    add_transaction(db, 40, datetime(2022, 9, 1, 1), "A1", "A2")

    assert await transaction_ids(db, alert_id=alert_id, account_id="A1") == [40, 30, 10, 20]


@pytest.mark.anyio
@pytest.mark.parametrize("field", ["start", "end"])
async def test_unparsable_timestamp_is_a_tool_error(db: Database, alert_id: int, field: str) -> None:
    async with Client(build_server(db.mcp_dsn)) as client:
        with pytest.raises(ToolError, match=f"{field} must be an ISO-8601 timestamp"):
            await client.call_tool("list_transactions", {"alert_id": alert_id, "account_id": "A1", field: "yesterday"})


@pytest.mark.anyio
async def test_timestamp_with_timezone_is_a_tool_error(db: Database, alert_id: int) -> None:
    async with Client(build_server(db.mcp_dsn)) as client:
        with pytest.raises(ToolError, match="end must not carry a timezone"):
            await client.call_tool(
                "list_transactions", {"alert_id": alert_id, "account_id": "A1", "end": "2022-09-02T12:00:00+00:00"}
            )


@pytest.mark.anyio
async def test_responses_never_mention_laundering(db: Database, alert_id: int) -> None:
    add_transaction(db, 11, datetime(2022, 9, 2, 10), "A1", "A2")
    with db.owner() as conn:
        conn.execute("INSERT INTO truth.transaction_labels VALUES (11, true)")

    async with Client(build_server(db.mcp_dsn)) as client:
        tools = await client.list_tools()
        alert = await client.call_tool("get_alert", {"alert_id": alert_id})
        page = await client.call_tool("list_transactions", {"alert_id": alert_id, "account_id": "A1"})

    everything = json.dumps(
        [
            [tool.model_dump(mode="json") for tool in tools],
            alert.structured_content,
            page.structured_content,
            [block.model_dump(mode="json") for block in alert.content + page.content],
        ]
    )
    assert "laundering" not in everything.lower()
    assert [tool.name for tool in tools] == ["get_alert", "list_transactions"]
