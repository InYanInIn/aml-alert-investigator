from collections import defaultdict
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from datetime import date, timedelta
from decimal import Decimal
from typing import Literal, TypedDict
from uuid import UUID, uuid4

from psycopg import Connection
from psycopg.rows import TupleRow, class_row
from psycopg.types.json import Jsonb

from aml_investigator.core.sql import load
from aml_investigator.evaluation.systems import EvalAlert, Recommendation
from aml_investigator.simulation.loop import Actor
from aml_investigator.simulation.segments import SegmentThresholds, segments_path
from aml_investigator.simulation.world import FIRST_DAY, MONITORED_BANK_ID, World, history_days

DEVELOPMENT_DATABASE = "aml_sim_dev"
type UiSystem = Literal["b4", "azure_profile"]
type PeriodStatus = Literal["running", "done", "failed"]
type SkipReason = Literal["investigation_cap", "prior_paid_attempt"]

LOCK_JOB = load(__package__, "lock_ui_job")
RESET_WORLD = load(__package__, "reset_ui_world")
INSERT_WORLD = load(__package__, "insert_ui_world")
READ_WORLD = load(__package__, "read_ui_world")
INSERT_DISPLAY = load(__package__, "insert_account_display")
READ_PERIOD = load(__package__, "read_latest_period")
INSERT_HISTORY = load(__package__, "insert_history_period")
START_PERIOD = load(__package__, "start_ui_period")
PUBLISH_DAY = load(__package__, "publish_ui_day")
FINISH_PERIOD = load(__package__, "finish_ui_period")
READ_BLOCKS = load(__package__, "read_ui_blocks")
INSERT_ALERTS = load(__package__, "insert_ui_alerts")
INSERT_DECISION = load(__package__, "insert_ui_decision")
INSERT_BLOCKS = load(__package__, "insert_ui_blocks")
COUNT_MONITORED = load(__package__, "count_ui_monitored")
READ_SPEND = load(__package__, "read_azure_spend")
READ_ATTEMPT = load(__package__, "read_azure_attempt")


class StoredThresholds(TypedDict):
    suppression_days: int
    thresholds: dict[str, dict[str, float]]


@dataclass(frozen=True)
class StoredWorld:
    seed: int
    generation: UUID
    persona_counts: dict[str, int]
    segment_thresholds: StoredThresholds


@dataclass(frozen=True)
class Period:
    period_id: int
    first_day: date
    last_day: date
    target_day: date
    system: UiSystem | Literal["history"]
    investigation_cap: int
    cost_usd: Decimal
    investigations: int
    status: PeriodStatus
    tape_sha256: str
    truncated: bool
    error: str | None


class Periods:
    def __init__(self, conn: Connection[TupleRow]) -> None:
        self.conn = conn

    def lock(self) -> bool:
        [(locked,)] = self.conn.execute(LOCK_JOB).fetchall()
        return bool(locked)

    def world(self) -> StoredWorld | None:
        with self.conn.cursor(row_factory=class_row(StoredWorld)) as cursor:
            return cursor.execute(READ_WORLD).fetchone()

    def create_world(self, world: World, segments: SegmentThresholds) -> None:
        counts = {name: persona.accounts for name, persona in world.config.personas.personas.items()}
        self.conn.execute(
            INSERT_WORLD,
            (world.seed, uuid4(), Jsonb(counts), str(segments_path(DEVELOPMENT_DATABASE)), Jsonb(asdict(segments))),
        )
        by_kind = {account: kind for kind, accounts in world.external_accounts.items() for account in accounts}
        monitored = list(world.opening_balances)
        external = [account.account_id for account in world.externals]
        self.conn.execute(
            INSERT_DISPLAY,
            (
                monitored + external,
                [Decimal(str(world.opening_balances[account])) for account in monitored] + [None] * len(external),
                [None] * len(monitored) + [by_kind.get(account, "other_external") for account in external],
            ),
        )

    def history(self, digest: str) -> None:
        self.conn.execute(INSERT_HISTORY, {"first": FIRST_DAY, "last": history_days()[-1], "hash": digest})

    def latest(self) -> Period | None:
        with self.conn.cursor(row_factory=class_row(Period)) as cursor:
            return cursor.execute(READ_PERIOD).fetchone()

    def start(self, previous: Period, days: int, system: UiSystem, cap: int) -> Period:
        with self.conn.cursor(row_factory=class_row(Period)) as cursor:
            cursor.execute(
                START_PERIOD,
                {
                    "first": previous.last_day + timedelta(days=1),
                    "last": previous.last_day,
                    "target": previous.last_day + timedelta(days=days),
                    "system": system,
                    "cap": cap,
                    "hash": previous.tape_sha256,
                },
            )
            [period] = cursor.fetchall()
        return period

    def blocks(self, actors: dict[str, Actor]) -> dict[date, list[Actor]]:
        blocks: dict[date, list[Actor]] = defaultdict(list)
        for account, day in self.conn.execute(READ_BLOCKS):
            blocks[day].append(actors[account])
        return dict(blocks)

    def alerts(self, period_id: int, alerts: Sequence[EvalAlert]) -> None:
        self.conn.execute(INSERT_ALERTS, (period_id, [alert.alert_id for alert in alerts]))

    def decision(
        self, alert_id: int, recommendation: Recommendation | None, skip_reason: SkipReason | None = None
    ) -> None:
        self.conn.execute(INSERT_DECISION, (alert_id, recommendation, skip_reason))

    def publish(
        self, period_id: int, day: date, digest: str, blocked: set[str], investigations: int, truncated: bool
    ) -> None:
        self.conn.execute(INSERT_BLOCKS, (day, period_id, sorted(blocked)))
        self.conn.execute(
            PUBLISH_DAY,
            {
                "period": period_id,
                "day": day,
                "hash": digest,
                "investigations": investigations,
                "truncated": truncated,
            },
        )

    def finish(self, error: str | None = None) -> None:
        self.conn.execute(FINISH_PERIOD, {"error": error})

    def monitored_accounts(self) -> int:
        [(count,)] = self.conn.execute(COUNT_MONITORED, (MONITORED_BANK_ID,)).fetchall()
        return int(count)

    def spent(self) -> Decimal:
        [(spent,)] = self.conn.execute(READ_SPEND).fetchall()
        return Decimal(spent)

    def was_paid_attempted(self, alert: EvalAlert) -> bool:
        [(attempted,)] = self.conn.execute(READ_ATTEMPT, (alert.account_id, alert.alert_date)).fetchall()
        return bool(attempted)
