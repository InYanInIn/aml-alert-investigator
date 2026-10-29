from typing import Any

import psycopg
from psycopg.rows import dict_row

Row = dict[str, Any]

QUEUE_SQL = """
SELECT a.alert_id,
       a.account_id,
       a.alert_date,
       coalesce(
           (SELECT string_agg(h.rule_id || ' (' || h.score || ')', ', ' ORDER BY h.rule_id)
            FROM core.alert_rule_hits h WHERE h.alert_id = a.alert_id),
           ''
       ) AS rule_hits,
       CASE
           WHEN i.investigation_id IS NULL THEN 'none'
           WHEN d.final_decision IS NULL THEN 'awaiting review'
           ELSE 'decided: ' || d.final_decision
       END AS status
FROM core.alerts a
LEFT JOIN LATERAL (
    SELECT investigation_id FROM review.investigations
    WHERE alert_id = a.alert_id ORDER BY investigation_id DESC LIMIT 1
) i ON true
LEFT JOIN review.decisions d ON d.investigation_id = i.investigation_id
ORDER BY a.alert_date DESC, a.alert_id
"""

ALERT_SQL = "SELECT alert_id, account_id, alert_date, window_start, window_end FROM core.alerts WHERE alert_id = %s"

RULE_HITS_SQL = "SELECT rule_id, score, evidence_txn_ids FROM core.alert_rule_hits WHERE alert_id = %s ORDER BY rule_id"

LATEST_INVESTIGATION_SQL = """
SELECT i.investigation_id, i.recommendation, i.decision, i.verification, i.created_at,
       d.final_decision, d.analyst, d.comment, d.decided_at
FROM review.investigations i
LEFT JOIN review.decisions d ON d.investigation_id = i.investigation_id
WHERE i.alert_id = %s
ORDER BY i.investigation_id DESC
LIMIT 1
"""

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
