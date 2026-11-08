import random
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import date, timedelta

from aml_investigator.simulation.world import (
    MIN_PAYMENT_EUR,
    Balances,
    GroupRoster,
    Payment,
    World,
    live_days,
    make_payment,
)

REPLACEMENT_DAYS = 2
ONE_DAY = timedelta(days=1)


@dataclass(frozen=True)
class GroupProgress:
    group_id: int
    scenario: str
    typology: str
    first_payment_day: date | None
    first_block_day: date | None
    stopped_on: date | None
    sink_eur: float
    planned_sink_eur: float

    @property
    def days_to_first_block(self) -> int | None:
        if self.first_payment_day is None or self.first_block_day is None:
            return None
        return (self.first_block_day - self.first_payment_day).days


class GroupRun:
    def __init__(self, world: World, roster: GroupRoster) -> None:
        self.world = world
        self.roster = roster
        self.scenario = roster.scenario
        self.active = {role: list(accounts) for role, accounts in roster.active.items()}
        self.reserves = {role: list(accounts) for role, accounts in roster.reserves.items()}
        self.sinks = {role.name for role in self.scenario.roles if role.kind == "sink"}
        self.next_day: date | None = live_days()[self.scenario.start_day]
        self.round_number = 0
        self.step_number = 0
        self.pots: defaultdict[str, float] = defaultdict(float)
        self.vacancies: list[tuple[str, int]] = []
        self.resume_on: date | None = None
        self.first_payment_day: date | None = None
        self.first_block_day: date | None = None
        self.stopped_on: date | None = None
        self.sink_eur = 0.0

    def payments_on(self, day: date) -> list[Payment]:
        if self.stopped_on is not None or self.next_day is None:
            return []
        if self.resume_on is not None:
            if day < self.resume_on:
                return []
            self.fill_vacancies()
            self.next_day = max(self.next_day, day)
        made: list[Payment] = []
        while self.next_day == day:
            made += self.execute_step(day)
            self.advance(day)
        if made and self.first_payment_day is None:
            self.first_payment_day = day
        return made

    def execute_step(self, day: date) -> list[Payment]:
        step = self.scenario.steps[self.step_number]
        senders, receivers = self.active[step.sender], self.active[step.receiver]
        payments_per_sender = Counter(senders[index % len(senders)] for index in range(step.count))
        held = dict(self.pots)
        made = []
        for index in range(step.count):
            rng = random.Random(  # noqa: S311
                f"{self.world.seed}|group|{self.roster.group_id}|{self.round_number}|{self.step_number}|{index}"
            )
            sender = senders[index % len(senders)]
            receiver = receivers[index % len(receivers)]
            eur = (
                step.amount.draw(rng)
                if step.amount is not None
                else held.get(sender, 0.0) * step.share / payments_per_sender[sender]
            )
            if eur < MIN_PAYMENT_EUR:
                continue
            made.append(
                make_payment(
                    self.world,
                    rng,
                    day,
                    sender,
                    receiver,
                    eur,
                    step.format,
                    step.reference,
                    attempt_id=self.roster.group_id,
                    typology=self.scenario.typology,
                )
            )
            self.pots[sender] -= eur
            self.pots[receiver] += eur
            if step.receiver in self.sinks:
                self.sink_eur += made[-1].amount_paid_eur
        return made

    def advance(self, day: date) -> None:
        self.step_number += 1
        if self.step_number < len(self.scenario.steps):
            self.next_day = day + timedelta(days=self.scenario.steps[self.step_number].delay)
            return
        self.step_number = 0
        self.round_number += 1
        finished = self.round_number == self.scenario.rounds
        self.next_day = None if finished else day + timedelta(days=self.scenario.round_gap)

    def block(self, account: str, decided_on: date) -> None:
        blocked_from = decided_on + ONE_DAY
        self.first_block_day = self.first_block_day or blocked_from
        for reserves in self.reserves.values():
            if account in reserves:
                reserves.remove(account)
                return
        for role, accounts in self.active.items():
            if account in accounts:
                self.vacate(role, accounts.index(account), blocked_from)

    def vacate(self, role: str, slot: int, blocked_from: date) -> None:
        waiting = sum(1 for vacant_role, _ in self.vacancies if vacant_role == role)
        if len(self.reserves[role]) <= waiting:
            self.stopped_on = blocked_from
            return
        self.vacancies.append((role, slot))
        self.resume_on = blocked_from + timedelta(days=REPLACEMENT_DAYS)

    def fill_vacancies(self) -> None:
        for role, slot in self.vacancies:
            self.active[role][slot] = self.reserves[role].pop(0)
        self.vacancies = []
        self.resume_on = None


def planned_sink_eur(world: World, roster: GroupRoster) -> float:
    run = GroupRun(world, roster)
    for day in live_days():
        run.payments_on(day)
    return run.sink_eur


class Laundering:
    def __init__(self, world: World) -> None:
        self.runs = {roster.group_id: GroupRun(world, roster) for roster in world.rosters}
        self.planned = {roster.group_id: planned_sink_eur(world, roster) for roster in world.rosters}

    def payments_on(self, day: date, balances: Balances) -> list[Payment]:
        payments = [payment for run in self.runs.values() for payment in run.payments_on(day)]
        for payment in payments:
            balances.settle(payment)
        return payments

    def block(self, group_id: int, account: str, decided_on: date) -> None:
        self.runs[group_id].block(account, decided_on)

    def progress(self) -> tuple[GroupProgress, ...]:
        return tuple(
            GroupProgress(
                group_id,
                run.scenario.id,
                run.scenario.typology,
                run.first_payment_day,
                run.first_block_day,
                run.stopped_on,
                run.sink_eur,
                self.planned[group_id],
            )
            for group_id, run in self.runs.items()
        )
