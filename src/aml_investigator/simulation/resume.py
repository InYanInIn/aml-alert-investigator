from collections.abc import Iterator, Mapping
from contextlib import nullcontext
from dataclasses import replace
from datetime import date, timedelta

from aml_investigator.core.db import FixedRole
from aml_investigator.core.errors import DomainError
from aml_investigator.data.splits import DayRange
from aml_investigator.evaluation.runner import final_recommendation
from aml_investigator.evaluation.systems import System
from aml_investigator.simulation.azure_spend import AzureSpendTransport
from aml_investigator.simulation.loop import DailyLoop, WorldDays, actors_on_connection, prepare_alerts
from aml_investigator.simulation.periods import Period, Periods, StoredWorld
from aml_investigator.simulation.segments import SegmentThresholds
from aml_investigator.simulation.world import FIRST_DAY, World, build_world, load_config


class WorldInputError(DomainError):
    pass


class WorldHashMismatchError(DomainError):
    pass


def days_between(first: date, last: date) -> Iterator[date]:
    for index in range((last - first).days + 1):
        yield first + timedelta(days=index)


def configured_world(seed: int, counts: Mapping[str, int]) -> World:
    config = load_config()
    if not 0 <= seed < 2**63:
        raise WorldInputError("Seed must be a non-negative 64-bit integer.")
    if counts.keys() - config.personas.personas.keys() or any(count <= 0 for count in counts.values()):
        raise WorldInputError("Use a positive account count for each supported persona.")
    personas = {
        name: persona.model_copy(update={"accounts": counts.get(name, persona.accounts)})
        for name, persona in config.personas.personas.items()
    }
    return build_world(seed, replace(config, personas=config.personas.model_copy(update={"personas": personas})))


def restore_loop(periods: Periods, world: StoredWorld, period: Period) -> DailyLoop:
    days = WorldDays(periods.conn, configured_world(world.seed, world.persona_counts))
    actors = actors_on_connection(periods.conn)
    segments = SegmentThresholds(**world.segment_thresholds)
    loop = DailyLoop(days, actors, segments, cadence=1, limit=period.investigation_cap)
    loop.replay(days_between(FIRST_DAY, period.last_day), periods.blocks(actors))
    if days.tape.sha256 != period.tape_sha256:
        raise WorldHashMismatchError("World no longer reproducible.")
    return loop


async def advance_day(
    periods: Periods,
    period: Period,
    loop: DailyLoop,
    day: date,
    system: System,
    remaining: int,
    spend: AzureSpendTransport | None,
    mcp: FixedRole,
) -> int:
    with periods.conn.transaction():
        loop.days.generate(day)
        alerts = prepare_alerts(periods.conn, DayRange(day, day), loop.segments)
        periods.alerts(period.period_id, alerts)
        blocked = set(loop.days.blocked)
        investigated = 0
        truncated = False
        for alert in alerts:
            if periods.was_paid_attempted(alert):
                periods.decision(alert.alert_id, None, "prior_paid_attempt")
                truncated = True
                continue
            if period.system == "azure_profile" and investigated == remaining:
                periods.decision(alert.alert_id, None, "investigation_cap")
                truncated = True
                continue
            try:
                with spend.alert(alert, mcp) if spend is not None else nullcontext():
                    recommendation = final_recommendation(await system.decide(alert), None)
            finally:
                mcp.raise_if_failed()
            periods.decision(alert.alert_id, recommendation)
            loop.apply_recommendation(alert, recommendation, day)
            investigated += int(period.system == "azure_profile")
        periods.publish(
            period.period_id, day, loop.days.tape.sha256, loop.days.blocked.keys() - blocked, investigated, truncated
        )
    return investigated
