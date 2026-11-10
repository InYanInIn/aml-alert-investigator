import json
import logging
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import asdict, dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import psycopg
from psycopg.rows import class_row

from aml_investigator.core.settings import Settings
from aml_investigator.core.sql import load
from aml_investigator.data.splits import DayRange
from aml_investigator.evaluation.baselines import open_system
from aml_investigator.evaluation.baselines.runtime import RuntimeSettings
from aml_investigator.evaluation.metrics import OperatingPoint, choose_operating_point
from aml_investigator.evaluation.results import RESULTS_DIR, THRESHOLDS_DIR, AlertTruth, validation_operating_point
from aml_investigator.evaluation.runner import decide_all, final_recommendation, read_truth, scored_alerts
from aml_investigator.evaluation.systems import EvalAlert, Recommendation, System, SystemDecision
from aml_investigator.monitoring.alerts import build_alerts_for_days
from aml_investigator.monitoring.calibration import choose_thresholds, train_split, write_thresholds
from aml_investigator.monitoring.rules import generate_candidates, rules
from aml_investigator.simulation import MAX_INVESTIGATIONS
from aml_investigator.simulation.behaviour import normal_day
from aml_investigator.simulation.laundering import GroupProgress, Laundering
from aml_investigator.simulation.segments import (
    SegmentThresholds,
    apply_segment_thresholds,
    calibrate_segments,
    load_segments,
    write_segments,
)
from aml_investigator.simulation.world import (
    LIVE_DAYS,
    MONITORED_BANK_ID,
    Balances,
    Tape,
    World,
    build_world,
    history_days,
    insert_population,
    live_days,
    load_config,
    require_simulation_database,
    reset_world,
)
from aml_investigator.truth.alert_truth import compute_alert_truth

logger = logging.getLogger(__name__)

SUPPRESSION_DAYS = 3
SHADOW_SYSTEM = "b4"
AGENT_SYSTEMS = frozenset({"scripted", "azure", "azure_profile"})

DELETE_EXTERNAL_CANDIDATES_SQL = load(__package__, "delete_external_candidates")
ACTORS_SQL = load(__package__, "actors")
ALERTS_BY_ID_SQL = load(__package__, "alerts_by_id")
ZERO_THRESHOLDS = {rule.RULE_ID: 0.0 for rule in rules()}


@dataclass(frozen=True)
class Actor:
    account_id: str
    persona: str
    group_id: int | None
    scenario: str | None


@dataclass(frozen=True)
class Decided:
    alert: EvalAlert
    decision: SystemDecision
    recommendation: Recommendation
    shadow: Recommendation | None
    decided_on: date


@dataclass(frozen=True)
class Case:
    alert: EvalAlert
    decision: SystemDecision
    recommendation: Recommendation
    shadow: Recommendation | None
    decided_on: date
    truth: AlertTruth
    actor: Actor


@dataclass(frozen=True)
class SimulationRun:
    database: str
    seed: int
    system: str
    cadence: int
    limit: int
    truncated: bool
    undecided_alerts: int
    last_day: date
    tape_sha256: str
    cases: tuple[Case, ...]
    groups: tuple[GroupProgress, ...]
    blocked: dict[str, date]


def regenerate_candidates(conn: psycopg.Connection[Any]) -> None:
    generate_candidates(conn)
    conn.execute(DELETE_EXTERNAL_CANDIDATES_SQL, {"monitored_bank_id": MONITORED_BANK_ID})


def monitoring_day(index: int, cadence: int) -> bool:
    return index % cadence == 0 or index == LIVE_DAYS


def read_actors(conninfo: str) -> dict[str, Actor]:
    with psycopg.connect(conninfo) as conn, conn.cursor(row_factory=class_row(Actor)) as cursor:
        cursor.execute(ACTORS_SQL)
        return {actor.account_id: actor for actor in cursor.fetchall()}


def alerts_by_id(conn: psycopg.Connection[Any], alert_ids: Sequence[int]) -> list[EvalAlert]:
    with conn.cursor(row_factory=class_row(EvalAlert)) as cursor:
        cursor.execute(ALERTS_BY_ID_SQL, {"alert_ids": list(alert_ids)})
        return cursor.fetchall()


class WorldDays:
    def __init__(self, conn: psycopg.Connection[Any], world: World) -> None:
        self.conn = conn
        self.world = world
        self.tape = Tape(world.seed)
        self.balances = Balances(world.opening_balances)
        self.laundering = Laundering(world)
        self.blocked: dict[str, date] = {}

    def populate(self) -> None:
        reset_world(self.conn)
        insert_population(self.conn, self.world)

    def generate(self, day: date, *, write: bool = True) -> None:
        payments = normal_day(self.world, day, self.balances, self.blocked)
        self.tape.append(self.conn if write else None, [*payments, *self.laundering.payments_on(day, self.balances)])

    def block(self, actor: Actor, decided_on: date) -> None:
        if actor.group_id is not None and actor.account_id not in self.blocked:
            self.blocked[actor.account_id] = decided_on + timedelta(days=1)
            self.laundering.block(actor.group_id, actor.account_id, decided_on)


class DailyLoop:
    def __init__(
        self,
        days: WorldDays,
        actors: Mapping[str, Actor],
        segments: SegmentThresholds,
        cadence: int,
        limit: int,
    ) -> None:
        self.days = days
        self.actors = actors
        self.segments = segments
        self.cadence = cadence
        self.limit = limit
        self.decided: list[Decided] = []
        self.truncated = False
        self.undecided_alerts = 0
        self.last_day = history_days()[-1]

    async def run(self, system: System, shadow: System, point: OperatingPoint | None) -> None:
        self.replay(history_days(), write=True)
        await self.advance(live_days(), system, shadow, point)

    def replay(
        self, days: Iterable[date], blocks: Mapping[date, Sequence[Actor]] | None = None, *, write: bool = False
    ) -> None:
        scheduled = blocks or {}
        for day in days:
            self.days.generate(day, write=write)
            for actor in scheduled.get(day, ()):
                self.days.block(actor, day)

    async def advance(
        self,
        days: Iterable[date],
        system: System,
        shadow: System,
        point: OperatingPoint | None,
        *,
        stop_on_cap: bool = True,
    ) -> None:
        if stop_on_cap and self.truncated:
            return
        ordered_days = list(days)
        if not ordered_days:
            return
        since = ordered_days[0]
        for index, day in enumerate(ordered_days, start=1):
            self.days.generate(day)
            self.last_day = day
            if monitoring_day(index, self.cadence):
                await self.monitor(DayRange(since, day), system, shadow, point)
                since = day + timedelta(days=1)
            if stop_on_cap and self.truncated:
                return

    async def monitor(self, days: DayRange, system: System, shadow: System, point: OperatingPoint | None) -> None:
        conn = self.days.conn
        regenerate_candidates(conn)
        apply_segment_thresholds(conn, self.segments.thresholds)
        ids = build_alerts_for_days(conn, ZERO_THRESHOLDS, days, suppression_days=self.segments.suppression_days)
        alerts = alerts_by_id(conn, ids)
        is_agent = system.name in AGENT_SYSTEMS
        chosen = alerts[: self.limit - len(self.decided)] if is_agent else alerts
        self.undecided_alerts = len(alerts) - len(chosen)
        self.truncated = self.undecided_alerts > 0
        decisions = await decide_all(system, chosen)
        shadows = await decide_all(shadow, chosen) if is_agent else [None] * len(chosen)
        today = [
            Decided(
                alert,
                decision,
                final_recommendation(decision, point),
                None if shade is None else final_recommendation(shade, None),
                days.last,
            )
            for alert, decision, shade in zip(chosen, decisions, shadows, strict=True)
        ]
        self.apply_analyst(today)
        self.decided += today
        logger.info(
            "sim_day",
            extra={
                "fields": {
                    "day": days.last.isoformat(),
                    "alerts": len(chosen),
                    "blocked": len(self.days.blocked),
                    "decided_total": len(self.decided),
                    "cost_usd_total": sum(item.decision.cost_usd or 0.0 for item in self.decided),
                }
            },
        )

    def apply_analyst(self, today: Sequence[Decided]) -> None:
        for item in today:
            if item.recommendation == "escalate":
                self.days.block(self.actors[item.alert.account_id], item.decided_on)


async def simulate(
    settings: Settings, *, seed: int, system: str, cadence: int, limit: int = MAX_INVESTIGATIONS
) -> SimulationRun:
    require_simulation_database(settings.database)
    if limit > MAX_INVESTIGATIONS:
        raise ValueError(f"an agent run stops at {MAX_INVESTIGATIONS} investigations; got {limit}")
    segments = load_segments(settings.database)
    runtime = RuntimeSettings.of(settings)
    point = validation_operating_point(Path(), settings.database, system) if system == "b1" else None
    with psycopg.connect(settings.owner_conninfo, autocommit=True) as conn:
        days = WorldDays(conn, build_world(seed, load_config()))
        days.populate()
        actors = read_actors(settings.evaluator_conninfo)
        loop = DailyLoop(days, actors, segments, cadence, limit)
        async with open_system(system, runtime) as under_test, open_system(SHADOW_SYSTEM, runtime) as shadow:
            await loop.run(under_test, shadow, point)
        compute_alert_truth(conn)
    alerts = [item.alert for item in loop.decided]
    truths = await read_truth(settings.evaluator_conninfo, alerts)
    cases = tuple(
        Case(
            item.alert,
            item.decision,
            item.recommendation,
            item.shadow,
            item.decided_on,
            truth,
            actors[item.alert.account_id],
        )
        for item, truth in zip(loop.decided, truths, strict=True)
    )
    return SimulationRun(
        settings.database,
        seed,
        system,
        cadence,
        limit,
        loop.truncated,
        loop.undecided_alerts,
        loop.last_day,
        days.tape.sha256,
        cases,
        days.laundering.progress(),
        days.blocked,
    )


async def calibrate_world(settings: Settings, *, seed: int, budget_per_day: float) -> tuple[Path, Path]:
    require_simulation_database(settings.database)
    thresholds_path = THRESHOLDS_DIR / f"{settings.database}.json"
    with psycopg.connect(settings.owner_conninfo, autocommit=True) as conn:
        days = WorldDays(conn, build_world(seed, load_config()))
        days.populate()
        for day in (*history_days(), *live_days()):
            days.generate(day)
        regenerate_candidates(conn)
        train = train_split(conn)["train"]
        thresholds = choose_thresholds(conn, train, budget_per_day=budget_per_day, suppression_days=SUPPRESSION_DAYS)
        write_thresholds(
            thresholds_path, thresholds, budget_per_day=budget_per_day, suppression_days=SUPPRESSION_DAYS, train=train
        )
        segment_thresholds = calibrate_segments(
            conn, train, budget_per_day=budget_per_day, suppression_days=SUPPRESSION_DAYS
        )
        write_segments(
            settings.database,
            segment_thresholds,
            budget_per_day=budget_per_day,
            suppression_days=SUPPRESSION_DAYS,
            train=train,
        )
        apply_segment_thresholds(conn, segment_thresholds)
        live = DayRange(live_days()[0], live_days()[-1])
        alerts = alerts_by_id(
            conn, build_alerts_for_days(conn, ZERO_THRESHOLDS, live, suppression_days=SUPPRESSION_DAYS)
        )
        compute_alert_truth(conn)
    async with open_system("b1", RuntimeSettings.of(settings)) as b1:
        decisions = await decide_all(b1, alerts)
    truths = await read_truth(settings.evaluator_conninfo, alerts)
    point = choose_operating_point(scored_alerts(decisions, truths), {True: 1.0, False: 1.0})
    point_path = RESULTS_DIR / settings.database / "val" / "b1.json"
    point_path.parent.mkdir(parents=True, exist_ok=True)
    point_path.write_text(json.dumps({"operating_point": asdict(point)}, indent=2) + "\n")
    return thresholds_path, point_path
