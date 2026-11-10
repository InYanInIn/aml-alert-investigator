import asyncio
import logging
from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager, nullcontext, suppress
from dataclasses import dataclass, replace
from datetime import date, timedelta
from decimal import Decimal
from math import ceil
from typing import Literal

import httpx
import psycopg
from langchain_core.language_models import BaseChatModel
from psycopg.rows import TupleRow

from aml_investigator.core.db import FixedRole, ScopedDatabaseError
from aml_investigator.core.errors import DomainError
from aml_investigator.core.settings import Settings
from aml_investigator.corpus.embeddings import azure_embeddings
from aml_investigator.evaluation.baselines.agent import open_agent
from aml_investigator.evaluation.baselines.b4 import OwnMoneyRule
from aml_investigator.evaluation.baselines.runtime import RuntimeSettings
from aml_investigator.evaluation.systems import System
from aml_investigator.investigation.models.azure import azure_model
from aml_investigator.simulation import MAX_INVESTIGATIONS
from aml_investigator.simulation.azure_spend import (
    CHAT,
    AzureGuardError,
    AzureSpendTransport,
    DisabledSyncTransport,
    azure_origin,
    verify_azure_deployments,
)
from aml_investigator.simulation.loop import WorldDays
from aml_investigator.simulation.periods import (
    DEVELOPMENT_DATABASE,
    RESET_WORLD,
    Period,
    Periods,
    PeriodStatus,
    StoredWorld,
    UiSystem,
)
from aml_investigator.simulation.resume import (
    WorldHashMismatchError,
    advance_day,
    configured_world,
    days_between,
    finish_in_thread,
    restore_loop,
)
from aml_investigator.simulation.segments import load_segments
from aml_investigator.simulation.world import World, history_days, require_simulation_database

logger = logging.getLogger(__name__)

UI_DATABASE = "aml_sim_ui"
CALIBRATION_ACCOUNTS = 850
CALIBRATION_ALERTS_PER_DAY = 2
DISPLAY_PRICE = Decimal("0.07")
UI_RUN_DAYS = (1, 7, 30)
INTERRUPTED = "Simulation interrupted. The last completed day is available."
FAILED = "Simulation failed. The last completed day is available."


class JobConflictError(DomainError):
    pass


@dataclass(frozen=True)
class Estimate:
    expected_alerts: float
    cap: int
    cost_usd: float
    within_limit: bool


@dataclass(frozen=True)
class AlertProgress:
    number: int
    total: int


@dataclass(frozen=True)
class JobStatus:
    period_id: int
    status: PeriodStatus
    day: date
    days_total: int
    days_completed: int
    investigations: int
    cost_usd: float
    error: str | None
    alert_progress: AlertProgress | None


class SimulationJobs:
    def __init__(
        self,
        settings: Settings,
        *,
        profile_model: BaseChatModel | None = None,
        azure_transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        require_simulation_database(settings.database)
        if settings.database != UI_DATABASE and not settings.database.startswith("aml_sim_test_"):
            raise ValueError("UI simulation requires aml_sim_ui or a disposable aml_sim_test_* database.")
        self.settings = settings
        self._profile_model = profile_model
        self._azure_transport = azure_transport
        self._task: asyncio.Task[None] | None = None
        self._alert_progress: tuple[int, date, AlertProgress] | None = None

    async def create(self, seed: int, persona_counts: Mapping[str, int]) -> None:
        await asyncio.to_thread(self._create, seed, persona_counts)

    def _create(self, seed: int, counts: Mapping[str, int]) -> None:
        with self._locked_connection() as conn:
            world = configured_world(seed, counts)
            try:
                self._write_history(conn, world)
            except Exception as error:
                logger.error(
                    "simulation_history_failed",
                    extra={"fields": {"period_id": 0, "day": history_days()[-1], "error_class": type(error).__name__}},
                )
                raise JobConflictError(FAILED) from None

    def _write_history(self, conn: psycopg.Connection[TupleRow], world: World) -> None:
        periods = Periods(conn)
        segments = load_segments(DEVELOPMENT_DATABASE)
        with conn.transaction():
            conn.execute(RESET_WORLD)
            days = WorldDays(conn, world)
            days.populate()
            periods.create_world(world, segments)
            for day in history_days():
                days.generate(day)
            periods.history(days.tape.sha256)

    def estimate(self, days: int, system: UiSystem) -> Estimate:
        with self._connect() as conn:
            return self._estimate(Periods(conn), days, system)

    def _estimate(self, periods: Periods, days: int, system: UiSystem) -> Estimate:
        if days not in UI_RUN_DAYS:
            raise ValueError("Simulation days must be 1, 7 or 30.")
        expected = Decimal(CALIBRATION_ALERTS_PER_DAY * days * periods.monitored_accounts()) / CALIBRATION_ACCOUNTS
        cap = min(MAX_INVESTIGATIONS, ceil(expected * Decimal("1.5")))
        if system == "b4":
            return Estimate(float(expected), 0, 0.0, True)
        required = max(cap * DISPLAY_PRICE, CHAT.bound) if cap else Decimal(0)
        within_limit = periods.spent() + required <= self.settings.ui_spend_limit_usd
        return Estimate(float(expected), cap, float(expected * DISPLAY_PRICE), within_limit)

    def current(self) -> JobStatus | None:
        with self._connect() as conn:
            period = Periods(conn).latest()
        if period is None:
            return None
        active = self._alert_progress
        progress = (
            active[2]
            if period.status == "running"
            and active is not None
            and active[:2] == (period.period_id, period.last_day + timedelta(days=1))
            else None
        )
        return JobStatus(
            period.period_id,
            period.status,
            period.last_day,
            (period.target_day - period.first_day).days + 1,
            (period.last_day - period.first_day).days + 1,
            period.investigations,
            float(period.cost_usd),
            period.error,
            progress,
        )

    def spent(self) -> float:
        with self._connect() as conn:
            return float(Periods(conn).spent())

    def recover(self) -> None:
        try:
            conn = self._locked_connection()
        except JobConflictError:
            return
        with conn:
            Periods(conn).finish(INTERRUPTED)

    async def start(self, days: int, system: UiSystem) -> int:
        conn = self._locked_connection()
        try:
            periods = Periods(conn)
            periods.finish(INTERRUPTED)
            world, previous = periods.world(), periods.latest()
            if world is None or previous is None:
                raise JobConflictError("Create a world before starting the simulation.")
            estimate = self._estimate(periods, days, system)
            if not estimate.within_limit:
                raise JobConflictError("The Azure spend limit would be exceeded.")
            endpoint = await self._verified_endpoint() if system == "azure_profile" else None
            period = periods.start(previous, days, system, estimate.cap)
            self._task = asyncio.create_task(self._run(periods, world, period, endpoint))
            self._task.add_done_callback(lambda _: conn.close())
        except BaseException:
            conn.close()
            raise
        return period.period_id

    async def retry_investigation(self, alert_id: int, investigation_id: int) -> None:
        with self._locked_connection() as conn:
            periods = Periods(conn)
            world = periods.world()
            candidate = periods.retryable_alert(alert_id, investigation_id)
            if world is None or candidate is None:
                raise JobConflictError("Only the latest failed Azure investigation can be retried. Refresh the page.")
            if periods.spent() + CHAT.bound > self.settings.ui_spend_limit_usd:
                raise JobConflictError("The Azure spend limit would be exceeded.")
            alert, period_id = candidate
            endpoint = await self._verified_endpoint()
            lock = asyncio.Lock()
            app, mcp = FixedRole(conn, lock, "app"), FixedRole(conn, lock, "mcp")
            try:
                async with self._system(world, period_id, "azure_profile", endpoint, (app, mcp)) as (system, spend):
                    with conn.transaction():
                        offset = periods.paid_ordinal(alert)
                        try:
                            with spend.alert(alert, mcp, offset=offset) if spend is not None else nullcontext():
                                await system.decide(alert)
                        finally:
                            mcp.raise_if_failed()
            except AzureGuardError as error:
                raise JobConflictError(str(error)) from None
            except Exception as error:
                error_class = error.cause if isinstance(error, ScopedDatabaseError) else type(error).__name__
                logger.error(
                    "investigation_retry_failed", extra={"fields": {"alert_id": alert_id, "error_class": error_class}}
                )
                raise JobConflictError("The investigation could not be retried.") from None

    async def _verified_endpoint(self) -> httpx.URL | None:
        if self._profile_model is not None:
            return None
        try:
            endpoint = azure_origin(self.settings.azure_openai_endpoint)
            await asyncio.to_thread(verify_azure_deployments, endpoint)
        except AzureGuardError as error:
            raise JobConflictError(str(error)) from None
        return endpoint

    async def close(self) -> None:
        if self._task is not None:
            self._task.cancel()
            with suppress(asyncio.CancelledError):
                await self._task

    async def _run(self, periods: Periods, world: StoredWorld, period: Period, endpoint: httpx.URL | None) -> None:
        day = period.first_day

        def report_alert(number: int, total: int) -> None:
            self._alert_progress = (period.period_id, day, AlertProgress(number, total))

        try:
            loop = await finish_in_thread(restore_loop, periods, world, period)
            remaining = period.investigation_cap
            lock = asyncio.Lock()
            app, mcp = FixedRole(periods.conn, lock, "app"), FixedRole(periods.conn, lock, "mcp")
            async with self._system(world, period.period_id, period.system, endpoint, (app, mcp)) as (system, spend):
                for day in days_between(period.first_day, period.target_day):
                    self._alert_progress = None
                    remaining -= await advance_day(
                        periods, period, loop, day, system, remaining, spend, mcp, report_alert
                    )
                    await asyncio.sleep(0)
            periods.finish()
        except asyncio.CancelledError:
            periods.finish(INTERRUPTED)
            raise
        except Exception as error:
            error_class = (
                error.cause if isinstance(error, (AzureGuardError, ScopedDatabaseError)) else type(error).__name__
            )
            logger.error(
                "simulation_period_failed",
                extra={"fields": {"period_id": period.period_id, "day": day, "error_class": error_class}},
            )
            periods.finish(str(error) if isinstance(error, (AzureGuardError, WorldHashMismatchError)) else FAILED)
        finally:
            self._alert_progress = None
            periods.conn.close()

    @asynccontextmanager
    async def _system(
        self,
        world: StoredWorld,
        period_id: int,
        system: UiSystem | Literal["history"],
        endpoint: httpx.URL | None,
        roles: tuple[FixedRole, FixedRole],
    ) -> AsyncIterator[tuple[System, AzureSpendTransport | None]]:
        if system == "b4":
            yield OwnMoneyRule(roles[1]), None
            return
        runtime = replace(RuntimeSettings.of(self.settings), customer_profile=True, azure_endpoint=None)
        if self._profile_model is not None:
            async with open_agent(runtime, self._profile_model, system, transaction_roles=roles) as agent:
                yield agent, None
            return
        if endpoint is None:
            raise AzureGuardError("The Azure endpoint is unavailable.")
        with self._connect() as ledger, httpx.Client(transport=DisabledSyncTransport(), trust_env=False) as sync_client:
            spend = AzureSpendTransport(
                ledger,
                self.settings.ui_spend_limit_usd,
                endpoint,
                world.generation,
                period_id,
                self._azure_transport,
            )
            async with httpx.AsyncClient(transport=spend, trust_env=False) as async_client:
                model = azure_model(str(endpoint), sync_client=sync_client, async_client=async_client)
                embeddings = azure_embeddings(str(endpoint), sync_client=sync_client, async_client=async_client)
                async with open_agent(
                    runtime,
                    model,
                    system,
                    transaction_roles=roles,
                    embed_query=spend.embedder(embeddings.aembed_query),
                ) as agent:
                    yield agent, spend

    def _connect(self) -> psycopg.Connection[TupleRow]:
        return psycopg.connect(self.settings.owner_conninfo, autocommit=True)

    def _locked_connection(self) -> psycopg.Connection[TupleRow]:
        conn = self._connect()
        try:
            if not Periods(conn).lock():
                raise JobConflictError("A simulation job is already running.")
        except BaseException:
            conn.close()
            raise
        return conn
