import math
import random
from collections.abc import Collection
from datetime import date

from aml_investigator.simulation.world import (
    EURO,
    FIRST_DAY,
    MIN_PAYMENT_EUR,
    Balances,
    Confounder,
    Customer,
    MonthlyEvent,
    Payment,
    RecurringEvent,
    World,
    make_payment,
)

SELF = "self"
PEER = "peer"
SIBLING = "sibling"


def poisson(rng: random.Random, rate: float) -> int:
    limit = math.exp(-rate)
    count = 0
    product = rng.random()
    while product > limit:
        count += 1
        product *= rng.random()
    return count


def normal_day(world: World, day: date, balances: Balances, blocked: Collection[str]) -> list[Payment]:
    return [
        payment
        for customer in world.customers
        for account in customer.accounts
        if account not in blocked
        for payment in AccountDay(world, customer, account, day, balances, blocked).payments()
    ]


class AccountDay:
    def __init__(
        self, world: World, customer: Customer, account: str, day: date, balances: Balances, blocked: Collection[str]
    ) -> None:
        self.world = world
        self.customer = customer
        self.account = account
        self.day = day
        self.balances = balances
        self.blocked = blocked
        self.persona = world.config.personas.personas[customer.persona]
        self.rng = random.Random(f"{world.seed}|{account}|{day.isoformat()}")  # noqa: S311
        self.made: list[Payment] = []

    def payments(self) -> list[Payment]:
        for monthly in self.persona.monthly:
            if monthly.day_of_month == self.day.day:
                self.external_event(monthly)
        for recurring in self.persona.recurring:
            for _ in range(poisson(self.rng, recurring.rate_per_day)):
                self.external_event(recurring)
        self.peer_payments()
        mismatches = self.persona.mismatches if self.customer.mismatch else []
        for confounder in (*self.persona.confounders, *mismatches):
            if self.is_active(confounder) and self.rng.random() < confounder.rate_per_day:
                self.confound(confounder)
        return self.made

    def is_active(self, confounder: Confounder) -> bool:
        return confounder.first_day_index <= (self.day - FIRST_DAY).days <= confounder.last_day_index

    def external_event(self, event: MonthlyEvent | RecurringEvent) -> None:
        party = self.rng.choice(self.world.external_accounts[event.counterparty])
        sender, receiver = (party, self.account) if event.direction == "in" else (self.account, party)
        self.pay(sender, receiver, event.amount.draw(self.rng), event.format, event.reference)

    def peer_payments(self) -> None:
        peers = self.persona.peers
        if peers is None:
            return
        for _ in range(poisson(self.rng, peers.rate_per_day)):
            peer = self.party(PEER)
            amount = peers.amount.draw(self.rng)
            if peer is not None:
                self.pay(self.account, peer, amount, peers.format, peers.reference)

    def confound(self, confounder: Confounder) -> None:
        amount = confounder.amount.draw(self.rng)
        for leg in confounder.legs:
            sender, receiver = self.party(leg.sender), self.party(leg.receiver)
            if sender is None or receiver is None:
                return
            if not self.pay(sender, receiver, amount * leg.share, leg.format, leg.reference, leg.received_currency):
                return

    def party(self, name: str) -> str | None:
        if name == SELF:
            return self.account
        if name == SIBLING:
            siblings = [account for account in self.customer.accounts if account != self.account]
            return self.rng.choice(siblings) if siblings else None
        if name == PEER:
            peer = self.rng.choice(self.world.persona_accounts[self.customer.persona])
            return None if peer == self.account or peer in self.blocked else peer
        return self.rng.choice(self.world.external_accounts[name])

    def pay(
        self, sender: str, receiver: str, eur: float, payment_format: str, reference: str, received_currency: str = EURO
    ) -> bool:
        paid = self.balances.affordable(sender, eur)
        if paid < MIN_PAYMENT_EUR or receiver in self.blocked:
            return False
        payment = make_payment(
            self.world,
            self.rng,
            self.day,
            sender,
            receiver,
            paid,
            payment_format,
            reference,
            received_currency=received_currency,
        )
        self.balances.settle(payment)
        self.made.append(payment)
        return True
