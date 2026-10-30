from typing import Any

import psycopg
from psycopg.rows import dict_row

from aml_investigator.core.sql import load

Row = dict[str, Any]

QUEUE_SQL = load(__package__, "alert_queue")

ALERT_SQL = "SELECT alert_id, account_id, alert_date, window_start, window_end FROM core.alerts WHERE alert_id = %s"

RULE_HITS_SQL = "SELECT rule_id, score, evidence_txn_ids FROM core.alert_rule_hits WHERE alert_id = %s ORDER BY rule_id"

LATEST_INVESTIGATION_SQL = load(__package__, "latest_investigation")

INVESTIGATION_SQL = """
SELECT i.alert_id, i.thread_id, d.decision_id IS NOT NULL AS decided
FROM review.investigations i
LEFT JOIN review.decisions d ON d.investigation_id = i.investigation_id
WHERE i.investigation_id = %s
"""


async def connect(dsn: str) -> psycopg.AsyncConnection[Row]:
    return await psycopg.AsyncConnection.connect(dsn, autocommit=True, row_factory=dict_row)


async def alert_queue(dsn: str) -> list[Row]:
    async with await connect(dsn) as conn:
        cursor = await conn.execute(QUEUE_SQL)
        return await cursor.fetchall()


async def alert_page(dsn: str, alert_id: int) -> Row | None:
    async with await connect(dsn) as conn:
        alert = await (await conn.execute(ALERT_SQL, (alert_id,))).fetchone()
        if alert is None:
            return None
        rule_hits = await (await conn.execute(RULE_HITS_SQL, (alert_id,))).fetchall()
        investigation = await (await conn.execute(LATEST_INVESTIGATION_SQL, (alert_id,))).fetchone()
    return {"alert": alert, "rule_hits": rule_hits, "investigation": investigation}


async def find_investigation(dsn: str, investigation_id: int) -> Row | None:
    async with await connect(dsn) as conn:
        cursor = await conn.execute(INVESTIGATION_SQL, (investigation_id,))
        return await cursor.fetchone()
