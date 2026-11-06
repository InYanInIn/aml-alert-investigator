from datetime import datetime

from psycopg import AsyncConnection, sql
from psycopg.rows import DictRow

from aml_investigator.core.db import Pool
from aml_investigator.core.errors import DomainError
from aml_investigator.core.sql import load
from aml_investigator.corpus.embeddings import QueryEmbedder, vector_literal
from aml_investigator.mcp_server.schemas import (
    AccountProfile,
    Alert,
    Counterparty,
    CounterpartyPage,
    FundPath,
    FundTrace,
    OtherAccounts,
    PassageMatch,
    PaymentDirection,
    ReferenceVariant,
    RegulationMatches,
    RegulationPassage,
    RuleHit,
    TraceDirection,
    Transaction,
    TransactionPage,
    TransactionSelection,
    TypologyCandidate,
    TypologyCandidates,
)
from aml_investigator.monitoring.typologies import RULE_TYPOLOGIES

MAX_TRANSACTIONS = 50
MAX_TRANSACTION_IDS = 100
MAX_COUNTERPARTIES = 20
MAX_HOPS = 3
MAX_PATHS = 20
FIRST_HOP_BRANCHING = 20
LATER_HOP_BRANCHING = 5
MAX_PASSAGES = 5
PASSAGES_PER_DOCUMENT = 3
REGULATION_DOCUMENTS = ("AMLR", "PL-AML")
ALERT_SQL = load(__package__, "alert")
RULE_HITS_SQL = load(__package__, "alert_rule_hits")
ACCOUNT_PROFILE_SQL = load(__package__, "account_profile")
COUNTERPARTIES_SQL = load(__package__, "counterparties")
TRACE_SQL = {"forward": load(__package__, "trace_forward"), "backward": load(__package__, "trace_backward")}
SEARCH_REGULATIONS_SQL = load(__package__, "search_regulations")
SEARCH_REGULATIONS_VECTOR_SQL = load(__package__, "search_regulations_vector")
REGULATION_PASSAGE_SQL = load(__package__, "regulation_passage")


class CaseDataError(DomainError):
    pass


class RecordNotFoundError(CaseDataError):
    def __init__(self, kind: str, key: object) -> None:
        super().__init__(f"{kind} {key} does not exist.")


class InvalidArgumentError(CaseDataError):
    pass


class CaseStore:
    def __init__(
        self, pool: Pool, reference_variant: ReferenceVariant, embed_query: QueryEmbedder | None = None
    ) -> None:
        self.pool = pool
        self.embed_query = embed_query
        self.list_transactions_sql = with_reference("list_transactions", reference_variant)
        self.get_transactions_sql = with_reference("get_transactions", reference_variant)

    async def alert(self, alert_id: int) -> Alert:
        async with self.pool.connection() as conn:
            return await load_alert(conn, alert_id)

    async def transaction_page(
        self,
        alert_id: int,
        account_id: str,
        *,
        direction: PaymentDirection,
        start: str | None,
        end: str | None,
        min_amount: float,
        limit: int,
    ) -> TransactionPage:
        capped = clamped(limit, MAX_TRANSACTIONS)
        async with self.pool.connection() as conn:
            alert = await load_alert(conn, alert_id)
            payments = payment_filter(alert, account_id, direction, start, end)
            cursor = await conn.execute(
                self.list_transactions_sql, {**payments, "min_amount": min_amount, "fetch": capped + 1}
            )
            rows = await cursor.fetchall()
        return TransactionPage(
            as_of=alert.as_of,
            truncated=len(rows) > capped,
            transactions=[Transaction(**row) for row in rows[:capped]],
        )

    async def transaction_selection(self, alert_id: int, txn_ids: list[int]) -> TransactionSelection:
        requested = sorted(set(txn_ids))
        if len(requested) > MAX_TRANSACTION_IDS:
            raise InvalidArgumentError(
                f"At most {MAX_TRANSACTION_IDS} txn_ids per call, got {len(requested)}; split the list."
            )
        async with self.pool.connection() as conn:
            alert = await load_alert(conn, alert_id)
            cursor = await conn.execute(self.get_transactions_sql, {"txn_ids": requested, "as_of": alert.as_of})
            rows = await cursor.fetchall()
        return TransactionSelection(
            as_of=alert.as_of,
            omitted=len(requested) - len(rows),
            transactions=[Transaction(**row) for row in rows],
        )

    async def account_profile(self, alert_id: int, account_id: str) -> AccountProfile:
        async with self.pool.connection() as conn:
            alert = await load_alert(conn, alert_id)
            cursor = await conn.execute(
                ACCOUNT_PROFILE_SQL, {"account": account_id, "as_of": alert.as_of, "window_start": alert.window_start}
            )
            row = await cursor.fetchone()
        if row is None:
            raise RecordNotFoundError("Account", account_id)
        others = OtherAccounts(count=row.pop("other_account_count"), account_ids=row.pop("other_account_ids"))
        return AccountProfile(**row, as_of=alert.as_of, other_accounts_of_entity=others)

    async def counterparty_page(
        self, alert_id: int, account_id: str, *, direction: PaymentDirection, start: str | None, end: str | None
    ) -> CounterpartyPage:
        async with self.pool.connection() as conn:
            alert = await load_alert(conn, alert_id)
            payments = payment_filter(alert, account_id, direction, start, end)
            cursor = await conn.execute(COUNTERPARTIES_SQL, {**payments, "limit": MAX_COUNTERPARTIES})
            rows = await cursor.fetchall()
        total = int(rows[0]["counterparty_count"]) if rows else 0
        return CounterpartyPage(
            as_of=alert.as_of,
            counterparty_count=total,
            truncated=total > MAX_COUNTERPARTIES,
            counterparties=[Counterparty(**row) for row in rows],
        )

    async def fund_trace(
        self, alert_id: int, account_id: str, *, direction: TraceDirection, max_hops: int
    ) -> FundTrace:
        hops = clamped(max_hops, MAX_HOPS)
        async with self.pool.connection() as conn:
            alert = await load_alert(conn, alert_id)
            cursor = await conn.execute(
                TRACE_SQL[direction],
                {
                    "account": account_id,
                    "lower": alert.window_start,
                    "upper": alert.as_of,
                    "max_hops": hops,
                    "first_branching": FIRST_HOP_BRANCHING,
                    "branching": LATER_HOP_BRANCHING,
                    "limit": MAX_PATHS,
                },
            )
            rows = await cursor.fetchall()
        return FundTrace(
            as_of=alert.as_of,
            direction=direction,
            max_hops=hops,
            truncated=bool(rows) and int(rows[0]["path_count"]) > MAX_PATHS,
            paths=[FundPath(flow_eur=row["flow_eur"], hops=row["hops"]) for row in rows],
        )

    async def typology_candidates(self, alert_id: int) -> TypologyCandidates:
        alert = await self.alert(alert_id)
        return TypologyCandidates(alert_id=alert_id, candidates=[typology_candidate(hit) for hit in alert.rule_hits])

    async def regulation_matches(self, query: str, *, top_k: int) -> RegulationMatches:
        limit = clamped(top_k, MAX_PASSAGES)
        if self.embed_query is None:
            statements = [(SEARCH_REGULATIONS_SQL, {"query": query, "doc_id": None, "top_k": limit})]
        else:
            embedding = vector_literal(await self.embed_query(query))
            statements = [
                (SEARCH_REGULATIONS_VECTOR_SQL, {"embedding": embedding, "doc_id": doc_id, "top_k": limit})
                for doc_id in REGULATION_DOCUMENTS
            ]
        rows: list[DictRow] = []
        async with self.pool.connection() as conn:
            for statement, params in statements:
                cursor = await conn.execute(statement, params)
                rows += await cursor.fetchall()
        return RegulationMatches(query=query, passages=[PassageMatch(**row) for row in rows])

    async def regulation_passage(self, passage_id: str) -> RegulationPassage:
        async with self.pool.connection() as conn:
            cursor = await conn.execute(REGULATION_PASSAGE_SQL, {"passage_id": passage_id})
            row = await cursor.fetchone()
        if row is None:
            raise RecordNotFoundError("Passage", passage_id)
        return RegulationPassage(**row)


async def load_alert(conn: AsyncConnection[DictRow], alert_id: int) -> Alert:
    cursor = await conn.execute(ALERT_SQL, (alert_id,))
    alert = await cursor.fetchone()
    if alert is None:
        raise RecordNotFoundError("Alert", alert_id)
    cursor = await conn.execute(RULE_HITS_SQL, (alert_id,))
    return Alert(**alert, rule_hits=[RuleHit(**hit) for hit in await cursor.fetchall()])


def payment_filter(
    alert: Alert, account_id: str, direction: PaymentDirection, start: str | None, end: str | None
) -> dict[str, object]:
    return {
        "account": account_id,
        "lower": parse_timestamp("start", start) if start is not None else alert.window_start,
        "upper": min(parse_timestamp("end", end), alert.as_of) if end is not None else alert.as_of,
        "outgoing": direction in ("out", "both"),
        "incoming": direction in ("in", "both"),
    }


def parse_timestamp(name: str, value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        raise InvalidArgumentError(
            f"{name} must be an ISO-8601 timestamp such as 2022-09-01T00:00:00, got {value!r}."
        ) from None
    if parsed.tzinfo is not None:
        raise InvalidArgumentError(f"{name} must not carry a timezone offset, got {value!r}.")
    return parsed


def clamped(value: int, maximum: int) -> int:
    return min(max(value, 1), maximum)


def with_reference(name: str, variant: ReferenceVariant) -> sql.Composed:
    reference = sql.SQL(load(__package__, f"reference_{variant}").strip())
    return sql.SQL(load(__package__, name)).format(reference=reference)


def typology_candidate(hit: RuleHit) -> TypologyCandidate:
    typologies = RULE_TYPOLOGIES.get(hit.rule_id.split("_", 1)[0], [])
    return TypologyCandidate(**hit.model_dump(), typologies=typologies)
