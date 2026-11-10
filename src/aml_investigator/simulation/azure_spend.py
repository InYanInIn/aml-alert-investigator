import json
import re
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Literal
from uuid import UUID

import httpx
from azure.identity import DefaultAzureCredential
from psycopg import Connection
from psycopg.rows import TupleRow

from aml_investigator.core.db import FixedRole
from aml_investigator.core.errors import DomainError
from aml_investigator.core.sql import load
from aml_investigator.corpus.embeddings import EMBEDDING_MODEL, QueryEmbedder
from aml_investigator.evaluation.systems import EvalAlert
from aml_investigator.investigation.agent.evidence import REGULATION_QUERIES
from aml_investigator.investigation.agent.loop import MAX_TOOL_CALLS
from aml_investigator.investigation.agent.usage import TOKENS_PER_PRICE_UNIT
from aml_investigator.investigation.models.azure import API_VERSION, AZURE_MODEL, UI_COMPLETION_LIMIT
from aml_investigator.simulation.periods import Periods

LOCK_SPEND = load(__package__, "lock_azure_spend")
RESERVE = load(__package__, "reserve_azure_spend")
SETTLE = load(__package__, "settle_azure_spend")
AZURE_HOST = re.compile(r"aml-investigator-[a-f0-9]{8}\.openai\.azure\.com")
AZURE_ACCOUNT = "aml-investigator-openai"
AZURE_RESOURCE_GROUP = "rg-aml-investigator"
ARM_ORIGIN = "https://management.azure.com"
ARM_SCOPE = f"{ARM_ORIGIN}/.default"
ARM_API_VERSION = "2024-10-01"
ARM_TIMEOUT_SECONDS = 10
ARM_MAX_BYTES = 65_536
METADATA_REFUSED = "Azure deployment metadata does not match the supported AML deployments."
METADATA_UNAVAILABLE = "Azure deployment metadata could not be verified."

type RequestKind = Literal["chat", "embedding"]


@dataclass(frozen=True)
class Tariff:
    model: str
    version: str
    sku: str
    input_limit: int
    output_limit: int
    input_price: Decimal
    output_price: Decimal
    max_requests: int

    def cost(self, input_tokens: int, output_tokens: int) -> Decimal:
        return (input_tokens * self.input_price + output_tokens * self.output_price) / TOKENS_PER_PRICE_UNIT

    @property
    def bound(self) -> Decimal:
        return self.cost(self.input_limit, self.output_limit)


CHAT = Tariff(
    AZURE_MODEL,
    "2025-08-07",
    "GlobalStandard",
    272_000,
    UI_COMPLETION_LIMIT,
    Decimal("0.30"),
    Decimal("2.40"),
    MAX_TOOL_CALLS + 3,
)
EMBEDDING = Tariff(
    EMBEDDING_MODEL,
    "1",
    "DataZoneStandard",
    8_192,
    0,
    Decimal("0.030"),
    Decimal(0),
    MAX_TOOL_CALLS + sum(len(queries) for queries in REGULATION_QUERIES.values()),
)
OPERATIONS: dict[str, RequestKind] = {
    f"/openai/deployments/{AZURE_MODEL}/chat/completions": "chat",
    f"/openai/deployments/{EMBEDDING_MODEL}/embeddings": "embedding",
}
TARIFFS = {"chat": CHAT, "embedding": EMBEDDING}


class AzureGuardError(DomainError):
    def __init__(self, message: str, *, cause: str = "AzureGuardError") -> None:
        super().__init__(message)
        self.cause = cause


def azure_origin(endpoint: str | None) -> httpx.URL:
    try:
        url = httpx.URL(endpoint or "")
    except httpx.InvalidURL:
        raise AzureGuardError("The Azure endpoint is invalid.") from None
    if (
        url.scheme != "https"
        or not AZURE_HOST.fullmatch(url.host)
        or url.port is not None
        or url.raw_path != b"/"
        or url.userinfo
        or url.fragment
    ):
        raise AzureGuardError("The Azure endpoint does not identify the supported AML deployments.")
    return url


def verify_azure_deployments(endpoint: httpx.URL, subscription_id: str | None) -> None:
    if not subscription_id:
        raise AzureGuardError(METADATA_UNAVAILABLE)
    account_path = (
        f"/subscriptions/{subscription_id}/resourceGroups/{AZURE_RESOURCE_GROUP}"
        f"/providers/Microsoft.CognitiveServices/accounts/{AZURE_ACCOUNT}"
    )
    account = _azure_metadata(account_path)
    properties = account.get("properties")
    if not isinstance(properties, dict):
        raise AzureGuardError(METADATA_REFUSED)
    reported = properties.get("endpoint")
    if (
        account.get("name") != AZURE_ACCOUNT
        or account.get("kind") != "OpenAI"
        or properties.get("provisioningState") != "Succeeded"
        or properties.get("customSubDomainName") != endpoint.host.split(".", 1)[0]
        or not isinstance(reported, str)
        or reported.removesuffix("/") != str(endpoint).removesuffix("/")
    ):
        raise AzureGuardError(METADATA_REFUSED)
    listing = _azure_metadata(f"{account_path}/deployments")
    deployments = listing.get("value")
    if not isinstance(deployments, list) or "nextLink" in listing:
        raise AzureGuardError(METADATA_REFUSED)
    for tariff in TARIFFS.values():
        matches = [item for item in deployments if isinstance(item, dict) and item.get("name") == tariff.model]
        deployment = matches[0] if len(matches) == 1 else {}
        sku = deployment.get("sku")
        found = deployment.get("properties")
        model = found.get("model") if isinstance(found, dict) else None
        if not isinstance(sku, dict) or not isinstance(found, dict) or not isinstance(model, dict):
            raise AzureGuardError(METADATA_REFUSED)
        reported_values = {
            "format": model.get("format"),
            "model": model.get("name"),
            "version": model.get("version"),
            "sku": sku.get("name"),
            "state": found.get("provisioningState"),
            "spillover": found.get("spilloverDeploymentName"),
        }
        expected = {
            "format": "OpenAI",
            "model": tariff.model,
            "version": tariff.version,
            "sku": tariff.sku,
            "state": "Succeeded",
            "spillover": None,
        }
        if found.get("serviceTier") not in (None, "Default") or reported_values != expected:
            raise AzureGuardError(METADATA_REFUSED)


def _azure_metadata(path: str) -> dict[str, object]:
    try:
        token = DefaultAzureCredential().get_token(ARM_SCOPE).token
        with httpx.Client(
            base_url=ARM_ORIGIN, timeout=ARM_TIMEOUT_SECONDS, follow_redirects=False, trust_env=False
        ) as client:
            response = client.get(
                path, params={"api-version": ARM_API_VERSION}, headers={"Authorization": f"Bearer {token}"}
            )
        response.raise_for_status()
        if len(response.content) > ARM_MAX_BYTES:
            raise AzureGuardError(METADATA_UNAVAILABLE)
        payload: object = response.json()
    except AzureGuardError:
        raise
    except Exception as error:
        raise AzureGuardError(METADATA_UNAVAILABLE, cause=type(error).__name__) from None
    if not isinstance(payload, dict):
        raise AzureGuardError(METADATA_REFUSED)
    return payload


@dataclass
class AlertSpend:
    alert: EvalAlert
    mcp: FixedRole
    offset: int = 0
    chat: int = 0
    embedding: int = 0
    failure: AzureGuardError | None = None

    def next_ordinal(self, kind: RequestKind) -> int:
        if kind == "chat":
            self.chat += 1
            count = self.chat
        else:
            self.embedding += 1
            count = self.embedding
        if count > TARIFFS[kind].max_requests:
            raise AzureGuardError("The Azure request limit for this alert was reached.")
        return self.offset + self.chat + self.embedding


class DisabledSyncTransport(httpx.BaseTransport):
    def handle_request(self, _request: httpx.Request) -> httpx.Response:
        raise AzureGuardError("The Azure simulation requires an asynchronous provider request.")


class AzureSpendTransport(httpx.AsyncBaseTransport):
    def __init__(
        self,
        conn: Connection[TupleRow],
        limit: Decimal,
        endpoint: httpx.URL,
        generation: UUID,
        period_id: int,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._conn = conn
        self._limit = limit
        self._endpoint = endpoint
        self._generation = generation
        self._period_id = period_id
        self._transport = transport if transport is not None else httpx.AsyncHTTPTransport(retries=0, trust_env=False)
        self._active: AlertSpend | None = None

    def embedder(self, embed: QueryEmbedder) -> QueryEmbedder:
        async def query(text: str) -> list[float]:
            active = self._request_scope()
            try:
                return await embed(text)
            except Exception as error:
                if active.failure is not None:
                    raise active.failure from None
                active.failure = AzureGuardError("The Azure embedding request failed.", cause=type(error).__name__)
                raise active.failure from None

        return query

    @contextmanager
    def alert(self, alert: EvalAlert, mcp: FixedRole, *, offset: int = 0) -> Iterator[None]:
        if self._active is not None:
            raise AzureGuardError("Another Azure investigation is active.")
        active = self._active = AlertSpend(alert, mcp, offset)
        try:
            yield
        finally:
            self._active = None
            if active.failure is not None:
                raise active.failure from None

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        active = self._request_scope()
        try:
            kind = self._request_kind(request)
            ordinal = active.next_ordinal(kind)
            identity = (*self._identity(active.alert), ordinal)
            tariff = TARIFFS[kind]
            self._reserve(identity, kind, tariff.bound)
            response = await self._transport.handle_async_request(request)
            if not response.is_success:
                await response.aclose()
                raise AzureGuardError("Azure refused the request. Its reservation was retained.")
            await response.aread()
            cost = usage_cost(response, tariff)
            if cost is not None:
                self._conn.execute(SETTLE, (cost, *identity))
            return response
        except AzureGuardError as error:
            active.failure = error
            raise
        except Exception as error:
            active.failure = AzureGuardError("The Azure request failed.", cause=type(error).__name__)
            raise active.failure from None

    async def aclose(self) -> None:
        await self._transport.aclose()

    def _request_scope(self) -> AlertSpend:
        active = self._active
        if active is None:
            raise AzureGuardError("An Azure request requires an active simulation alert.")
        active.mcp.raise_if_failed()
        if active.failure is not None:
            raise active.failure
        return active

    def _identity(self, alert: EvalAlert) -> tuple[UUID, str, date]:
        return self._generation, alert.account_id, alert.alert_date

    def _request_kind(self, request: httpx.Request) -> RequestKind:
        url = request.url
        kind = OPERATIONS.get(url.path)
        if (
            kind is None
            or request.method != "POST"
            or url.scheme != self._endpoint.scheme
            or url.netloc != self._endpoint.netloc
            or list(url.params.multi_items()) != [("api-version", API_VERSION)]
        ):
            raise AzureGuardError("The Azure request has no supported deployment tariff.")
        payload = json.loads(request.content)
        tariff = TARIFFS[kind]
        if not isinstance(payload, dict) or payload.get("model") != tariff.model:
            raise AzureGuardError("The Azure request does not match its deployment.")
        if kind == "chat":
            output = payload.get("max_completion_tokens")
            if (
                type(output) is not int
                or not 1 <= output <= tariff.output_limit
                or payload.get("n", 1) != 1
                or payload.get("stream")
                or payload.get("service_tier")
                or payload.get("modalities")
                or not all(text_message(item) for item in payload["messages"])
                or not all(tool.get("type") == "function" for tool in payload.get("tools", []))
            ):
                raise AzureGuardError("The Azure chat request exceeds the supported bounds.")
        else:
            inputs = payload.get("input")
            if (
                not isinstance(inputs, list)
                or len(inputs) != 1
                or not isinstance(inputs[0], str)
                or not 1 <= len(inputs[0].encode()) <= tariff.input_limit
            ):
                raise AzureGuardError("The Azure embedding request exceeds the supported bounds.")
        return kind

    def _reserve(self, identity: tuple[UUID, str, date, int], kind: RequestKind, bound: Decimal) -> None:
        with self._conn.transaction():
            self._conn.execute(LOCK_SPEND)
            if Periods(self._conn).spent() + bound > self._limit:
                raise AzureGuardError("The Azure spend limit would be exceeded.")
            self._conn.execute(RESERVE, (*identity, self._period_id, kind, bound))


def text_message(message: object) -> bool:
    if not isinstance(message, dict) or message.get("audio") is not None:
        return False
    content = message.get("content")
    return (
        content is None
        or isinstance(content, str)
        or (
            isinstance(content, list)
            and all(
                isinstance(block, dict) and block.get("type") == "text" and isinstance(block.get("text"), str)
                for block in content
            )
        )
    )


def usage_cost(response: httpx.Response, tariff: Tariff) -> Decimal | None:
    payload = response.json()
    if not isinstance(payload, dict) or "error" in payload:
        raise AzureGuardError("Azure returned an invalid response. Its reservation was retained.")
    model = payload.get("model")
    if model not in (tariff.model, f"{tariff.model}-{tariff.version}"):
        raise AzureGuardError("Azure returned an unsupported model. Its reservation was retained.")
    usage = payload.get("usage")
    if not isinstance(usage, dict):
        return None
    input_tokens = usage.get("prompt_tokens")
    output_tokens = usage.get("completion_tokens") if tariff.output_limit else 0
    if (
        type(input_tokens) is not int
        or type(output_tokens) is not int
        or not 0 <= input_tokens <= tariff.input_limit
        or not 0 <= output_tokens <= tariff.output_limit
    ):
        return None
    return tariff.cost(input_tokens, output_tokens)
