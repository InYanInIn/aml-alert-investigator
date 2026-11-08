import hashlib
import math
import random
import re
from collections.abc import Iterable, Mapping
from dataclasses import asdict, astuple, dataclass
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Any, Literal, Self

import polars as pl
import psycopg
import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

from aml_investigator.core.errors import DomainError
from aml_investigator.core.sql import load
from aml_investigator.data.ingest import SALT, copy_frame

SIMULATION_DATABASE = re.compile(r"aml_sim_[a-z0-9_]+")
CONFIG_DIR = Path("config/simulation")
FIRST_DAY = date(2025, 3, 3)
HISTORY_DAYS = 21
LIVE_DAYS = 28
TAPE_DAYS = HISTORY_DAYS + LIVE_DAYS
MONITORED_BANK_ID = 1
EXTERNAL_BANK_IDS = (2, 20)
INDIVIDUAL = "Individual"
EURO = "Euro"
MIN_PAYMENT_EUR = 1.0
REFERENCE_FIELDS = {"month": "March", "number": 1}
FIRST_PAYMENT_HOUR = 7
LAST_PAYMENT_HOUR = 20
OLDEST_CUSTOMER_DAYS = 3650
NEWEST_CUSTOMER_DAYS = 30

RESET_WORLD_SQL = load(__package__, "reset_world")
CLEAR_CORPUS_SQL = load(__package__, "clear_corpus")
COUNT_PASSAGES_SQL = load(__package__, "count_passages")
CORPUS_COPIES = (
    (load(__package__, "corpus_documents_out"), load(__package__, "corpus_documents_in")),
    (load(__package__, "corpus_passages_out"), load(__package__, "corpus_passages_in")),
)

ACCOUNT_SCHEMA = pl.Schema(
    {
        "account_id": pl.String(),
        "bank_id": pl.Int32(),
        "bank_name": pl.String(),
        "entity_id": pl.String(),
        "entity_type": pl.String(),
    }
)
ACTOR_SCHEMA = pl.Schema(
    {"account_id": pl.String(), "persona": pl.String(), "group_id": pl.Int32(), "scenario": pl.String()}
)
PROFILE_SCHEMA = pl.Schema(
    {
        "entity_id": pl.String(),
        "display_name": pl.String(),
        "occupation": pl.String(),
        "business_description": pl.String(),
        "account_purpose": pl.String(),
        "expected_activity": pl.String(),
        "declared_monthly_eur": pl.Float64(),
        "customer_since": pl.Date(),
    }
)
TRANSACTION_SCHEMA = pl.Schema(
    {
        "txn_id": pl.Int64(),
        "ts": pl.Datetime("us"),
        "from_account": pl.String(),
        "to_account": pl.String(),
        "amount_paid": pl.Float64(),
        "paid_currency": pl.String(),
        "amount_received": pl.Float64(),
        "received_currency": pl.String(),
        "payment_format": pl.String(),
        "amount_paid_eur": pl.Float64(),
        "reference": pl.String(),
    }
)
LABEL_SCHEMA = pl.Schema({"txn_id": pl.Int64(), "is_laundering": pl.Boolean()})
ATTEMPT_SCHEMA = pl.Schema({"attempt_id": pl.Int32(), "typology": pl.String(), "txn_id": pl.Int64()})


class NotASimulationDatabaseError(DomainError):
    def __init__(self, name: str) -> None:
        super().__init__(f"{name!r} is not a simulation world; world operations need a database named aml_sim_*")


def require_simulation_database(name: str) -> None:
    if not SIMULATION_DATABASE.fullmatch(name):
        raise NotASimulationDatabaseError(name)


def reset_world(conn: psycopg.Connection[Any]) -> None:
    require_simulation_database(conn.info.dbname)
    conn.execute(RESET_WORLD_SQL)


def copy_corpus(source: psycopg.Connection[Any], target: psycopg.Connection[Any]) -> int:
    require_simulation_database(target.info.dbname)
    with target.transaction():
        target.execute(CLEAR_CORPUS_SQL)
        for copy_out, copy_in in CORPUS_COPIES:
            with source.cursor().copy(copy_out) as out, target.cursor().copy(copy_in) as into:
                for chunk in out:
                    into.write(chunk)
    [(passages,)] = target.execute(COUNT_PASSAGES_SQL).fetchall()
    return int(passages)


def history_days() -> tuple[date, ...]:
    return tuple(FIRST_DAY + timedelta(days=offset) for offset in range(HISTORY_DAYS))


def live_days() -> tuple[date, ...]:
    return tuple(FIRST_DAY + timedelta(days=offset) for offset in range(HISTORY_DAYS, TAPE_DAYS))


class Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", populate_by_name=True)


class LogNormal(Frozen):
    median: float = Field(gt=0)
    sigma: float = Field(ge=0)

    def draw(self, rng: random.Random) -> float:
        return rng.lognormvariate(math.log(self.median), self.sigma)


class Span(Frozen):
    low: float = Field(gt=0)
    high: float = Field(gt=0)

    def draw(self, rng: random.Random) -> float:
        return rng.uniform(self.low, self.high)


class MonthlyEvent(Frozen):
    direction: Literal["in", "out"]
    counterparty: str
    day_of_month: int = Field(ge=1, le=28)
    amount: LogNormal
    format: str
    reference: str


class RecurringEvent(Frozen):
    direction: Literal["in", "out"]
    counterparty: str
    rate_per_day: float = Field(gt=0)
    amount: LogNormal
    format: str
    reference: str


class PeerEvent(Frozen):
    rate_per_day: float = Field(gt=0)
    amount: LogNormal
    format: str
    reference: str


class Leg(Frozen):
    sender: str = Field(alias="from")
    receiver: str = Field(alias="to")
    share: float = Field(default=1.0, gt=0, le=1)
    format: str
    reference: str
    received_currency: str = EURO


class Confounder(Frozen):
    name: str
    rate_per_day: float = Field(gt=0, le=1)
    amount: LogNormal
    legs: list[Leg] = Field(min_length=1)
    first_day_index: int = Field(default=0, ge=0)
    last_day_index: int = Field(default=TAPE_DAYS - 1, lt=TAPE_DAYS)


class Persona(Frozen):
    accounts: int = Field(gt=0)
    entity_types: list[str] = Field(min_length=1)
    accounts_per_customer: tuple[int, int]
    opening_balance: LogNormal
    declared_monthly: Span
    monthly: list[MonthlyEvent]
    recurring: list[RecurringEvent]
    peers: PeerEvent | None
    confounders: list[Confounder]
    mismatches: list[Confounder]


class ExternalKind(Frozen):
    count: int = Field(gt=0)
    entity_type: str


class PersonaFile(Frozen):
    fx_rates: dict[str, float]
    externals: dict[str, ExternalKind]
    personas: dict[str, Persona]


class Role(Frozen):
    name: str
    kind: Literal["member", "source", "sink"]
    count: int = Field(gt=0)
    persona: str | None = None
    entity_type: str | None = None
    reserves: int = Field(default=0, ge=0)

    @model_validator(mode="after")
    def member_or_external(self) -> Self:
        is_member = self.kind == "member"
        if is_member != (self.persona is not None) or is_member == (self.entity_type is not None):
            raise ValueError(f"role {self.name}: a member needs a persona, a source or sink needs an entity_type")
        if self.reserves and not is_member:
            raise ValueError(f"role {self.name}: only members have reserves")
        return self


class Step(Frozen):
    sender: str = Field(alias="from")
    receiver: str = Field(alias="to")
    amount: Span | None = None
    share: float = Field(default=0.0, ge=0, le=1)
    count: int = Field(default=1, gt=0)
    delay: int = Field(default=0, ge=0)
    format: str
    reference: str

    @model_validator(mode="after")
    def amount_or_share(self) -> Self:
        if (self.amount is None) == (self.share == 0):
            raise ValueError(f"step {self.sender}->{self.receiver}: give exactly one of amount and share")
        return self


class Scenario(Frozen):
    id: str
    typology: str
    start_day: int = Field(ge=0, lt=LIVE_DAYS)
    rounds: int = Field(default=1, gt=0)
    round_gap: int = Field(default=1, gt=0)
    roles: list[Role] = Field(min_length=2)
    steps: list[Step] = Field(min_length=1)

    @model_validator(mode="after")
    def steps_use_declared_roles(self) -> Self:
        kinds = {role.name: role.kind for role in self.roles}
        for step in self.steps:
            if kinds.get(step.sender) not in ("member", "source"):
                raise ValueError(f"scenario {self.id}: {step.sender} cannot send")
            if kinds.get(step.receiver) not in ("member", "sink"):
                raise ValueError(f"scenario {self.id}: {step.receiver} cannot receive")
        if self.steps[0].amount is None:
            raise ValueError(f"scenario {self.id}: the first step needs an amount; there is nothing to take a share of")
        return self


class ScenarioFile(Frozen):
    honest_profile_mismatch_share: float = Field(ge=0, le=1)
    scenarios: list[Scenario]


class ProfileTexts(Frozen):
    occupation: list[str] = Field(min_length=1)
    business_description: list[str] = Field(min_length=1)
    account_purpose: list[str] = Field(min_length=1)
    expected_activity: list[str] = Field(min_length=1)


class TextPools(Frozen):
    references: dict[str, list[str]]
    profiles: dict[str, ProfileTexts]
    company_names: list[str] = Field(min_length=1)

    @model_validator(mode="after")
    def references_render(self) -> Self:
        for texts in self.references.values():
            for text in texts:
                try:
                    text.format(**REFERENCE_FIELDS)
                except (KeyError, IndexError, ValueError) as error:
                    raise ValueError(f"reference text {text!r} uses a field other than month and number") from error
        return self


@dataclass(frozen=True)
class SimulationConfig:
    personas: PersonaFile
    scenarios: ScenarioFile
    pools: TextPools


def load_config(directory: Path = CONFIG_DIR) -> SimulationConfig:
    config = SimulationConfig(
        PersonaFile.model_validate(yaml.safe_load((directory / "personas.yaml").read_text())),
        ScenarioFile.model_validate(yaml.safe_load((directory / "scenarios.yaml").read_text())),
        TextPools.model_validate(yaml.safe_load((directory / "text_pools.yaml").read_text())),
    )
    require_shared_vocabulary(config)
    return config


def persona_reference_keys(persona: Persona) -> set[str]:
    keys = {event.reference for event in persona.monthly}
    keys |= {event.reference for event in persona.recurring}
    keys |= {persona.peers.reference} if persona.peers else set()
    keys |= {leg.reference for event in persona.confounders + persona.mismatches for leg in event.legs}
    return keys


def honest_reference_keys(personas: PersonaFile) -> set[str]:
    return {key for persona in personas.personas.values() for key in persona_reference_keys(persona)}


def require_shared_vocabulary(config: SimulationConfig) -> None:
    honest = honest_reference_keys(config.personas)
    steps = [(scenario, step) for scenario in config.scenarios.scenarios for step in scenario.steps]
    private = sorted({step.reference for _, step in steps} - honest)
    if private:
        raise ValueError(f"reference keys used only by launderers: {', '.join(private)}")
    keys = {name: persona_reference_keys(persona) for name, persona in config.personas.personas.items()}
    foreign = sorted(
        {
            f"{role.persona}:{step.reference}"
            for scenario, step in steps
            for role in scenario.roles
            if role.persona is not None
            and role.name in (step.sender, step.receiver)
            and step.reference not in keys[role.persona]
        }
    )
    if foreign:
        raise ValueError(f"reference keys no honest customer of that persona uses: {', '.join(foreign)}")
    missing = sorted((honest | {step.reference for _, step in steps}) - set(config.pools.references))
    if missing:
        raise ValueError(f"reference keys without texts: {', '.join(missing)}")


@dataclass(frozen=True)
class Customer:
    entity_id: str
    persona: str
    entity_type: str
    accounts: tuple[str, ...]
    mismatch: bool = False
    group_id: int | None = None
    scenario: str | None = None


@dataclass(frozen=True)
class ExternalAccount:
    account_id: str
    entity_id: str
    entity_type: str
    bank_id: int


@dataclass(frozen=True)
class CustomerProfile:
    entity_id: str
    display_name: str | None = None
    occupation: str | None = None
    business_description: str | None = None
    account_purpose: str | None = None
    expected_activity: str | None = None
    declared_monthly_eur: float | None = None
    customer_since: date | None = None


@dataclass(frozen=True)
class GroupRoster:
    group_id: int
    scenario: Scenario
    active: dict[str, tuple[str, ...]]
    reserves: dict[str, tuple[str, ...]]


@dataclass(frozen=True)
class World:
    seed: int
    config: SimulationConfig
    customers: tuple[Customer, ...]
    externals: tuple[ExternalAccount, ...]
    external_accounts: dict[str, tuple[str, ...]]
    persona_accounts: dict[str, tuple[str, ...]]
    rosters: tuple[GroupRoster, ...]
    profiles: tuple[CustomerProfile, ...]
    opening_balances: dict[str, float]


def pseudonym(prefix: str, seed: int, *key: object) -> str:
    text = "|".join(str(part) for part in (SALT, seed, *key))
    return prefix + hashlib.sha256(text.encode()).hexdigest()[:12]


class PopulationBuilder:
    def __init__(self, seed: int, config: SimulationConfig) -> None:
        self.seed = seed
        self.config = config
        self.rng = random.Random(seed)  # noqa: S311
        self.customers: list[Customer] = []
        self.externals: list[ExternalAccount] = []
        self.profiles: list[CustomerProfile] = []
        self.balances: dict[str, float] = {}

    def world(self) -> World:
        external_accounts = {
            kind: tuple(self.external(f"{kind}-{index}", spec.entity_type) for index in range(spec.count))
            for kind, spec in self.config.personas.externals.items()
        }
        self.honest_customers()
        rosters = tuple(self.roster(group_id, scenario) for group_id, scenario in enumerate(self.scenarios()))
        persona_accounts: dict[str, tuple[str, ...]] = dict.fromkeys(self.config.personas.personas, ())
        for customer in self.customers:
            persona_accounts[customer.persona] += customer.accounts
        return World(
            self.seed,
            self.config,
            tuple(self.customers),
            tuple(self.externals),
            external_accounts,
            persona_accounts,
            rosters,
            tuple(self.profiles),
            self.balances,
        )

    def scenarios(self) -> list[Scenario]:
        return self.config.scenarios.scenarios

    def honest_customers(self) -> None:
        share = self.config.scenarios.honest_profile_mismatch_share
        for name, persona in self.config.personas.personas.items():
            created, ordinal = 0, 0
            while created < persona.accounts:
                customer = self.customer(name, f"{name}-{ordinal}", mismatch=self.rng.random() < share)
                created += len(customer.accounts)
                ordinal += 1

    def roster(self, group_id: int, scenario: Scenario) -> GroupRoster:
        active: dict[str, tuple[str, ...]] = {}
        reserves: dict[str, tuple[str, ...]] = {}
        for role in scenario.roles:
            if role.persona is None:
                kind_key = f"group-{group_id}-{role.name}"
                active[role.name] = tuple(
                    self.external(f"{kind_key}-{index}", role.entity_type or INDIVIDUAL) for index in range(role.count)
                )
                reserves[role.name] = ()
                continue
            first_accounts = tuple(
                self.customer(
                    role.persona,
                    f"group-{group_id}-{role.name}-{index}",
                    group_id=group_id,
                    scenario=scenario.id,
                ).accounts[0]
                for index in range(role.count + role.reserves)
            )
            active[role.name] = first_accounts[: role.count]
            reserves[role.name] = first_accounts[role.count :]
        return GroupRoster(group_id, scenario, active, reserves)

    def customer(
        self,
        persona_name: str,
        key: str,
        *,
        mismatch: bool = False,
        group_id: int | None = None,
        scenario: str | None = None,
    ) -> Customer:
        persona = self.config.personas.personas[persona_name]
        low, high = persona.accounts_per_customer
        accounts = tuple(
            pseudonym("A", self.seed, "account", key, number) for number in range(self.rng.randint(low, high))
        )
        customer = Customer(
            pseudonym("E", self.seed, "customer", key),
            persona_name,
            self.rng.choice(persona.entity_types),
            accounts,
            mismatch,
            group_id,
            scenario,
        )
        self.customers.append(customer)
        self.profiles.append(self.profile(customer, persona))
        self.balances |= {account: persona.opening_balance.draw(self.rng) for account in accounts}
        return customer

    def profile(self, customer: Customer, persona: Persona) -> CustomerProfile:
        texts = self.config.pools.profiles[customer.persona]
        return CustomerProfile(
            customer.entity_id,
            self.company_name(customer.entity_type),
            self.rng.choice(texts.occupation),
            self.rng.choice(texts.business_description),
            self.rng.choice(texts.account_purpose),
            self.rng.choice(texts.expected_activity),
            round(persona.declared_monthly.draw(self.rng), 2),
            FIRST_DAY - timedelta(days=self.rng.randint(NEWEST_CUSTOMER_DAYS, OLDEST_CUSTOMER_DAYS)),
        )

    def company_name(self, entity_type: str) -> str | None:
        return None if entity_type == INDIVIDUAL else self.rng.choice(self.config.pools.company_names)

    def external(self, key: str, entity_type: str) -> str:
        account_id = pseudonym("A", self.seed, "external", key)
        entity_id = pseudonym("E", self.seed, "external", key)
        self.externals.append(ExternalAccount(account_id, entity_id, entity_type, self.rng.randint(*EXTERNAL_BANK_IDS)))
        name = self.company_name(entity_type)
        if name is not None:
            self.profiles.append(CustomerProfile(entity_id, name))
        return account_id


def build_world(seed: int, config: SimulationConfig) -> World:
    return PopulationBuilder(seed, config).world()


def insert_population(conn: psycopg.Connection[Any], world: World) -> None:
    accounts = [
        (account, MONITORED_BANK_ID, f"Bank {MONITORED_BANK_ID}", customer.entity_id, customer.entity_type)
        for customer in world.customers
        for account in customer.accounts
    ] + [
        (external.account_id, external.bank_id, f"Bank {external.bank_id}", external.entity_id, external.entity_type)
        for external in world.externals
    ]
    actors = [
        (account, customer.persona, customer.group_id, customer.scenario)
        for customer in world.customers
        for account in customer.accounts
    ]
    rates = pl.DataFrame(
        list(world.config.personas.fx_rates.items()),
        schema={"currency": pl.String(), "eur_per_unit": pl.Float64()},
        orient="row",
    )
    copy_frame(conn, "core", "accounts", pl.DataFrame(sorted(accounts), schema=ACCOUNT_SCHEMA, orient="row"))
    copy_frame(conn, "core", "fx_rates", rates)
    copy_frame(
        conn,
        "core",
        "customer_profiles",
        pl.DataFrame([asdict(profile) for profile in world.profiles], schema=PROFILE_SCHEMA),
    )
    copy_frame(conn, "truth", "sim_actors", pl.DataFrame(sorted(actors), schema=ACTOR_SCHEMA, orient="row"))


@dataclass(frozen=True)
class Payment:
    ts: datetime
    from_account: str
    to_account: str
    amount_paid: float
    paid_currency: str
    amount_received: float
    received_currency: str
    payment_format: str
    amount_paid_eur: float
    reference: str
    attempt_id: int | None = None
    typology: str | None = None

    @property
    def is_laundering(self) -> bool:
        return self.attempt_id is not None

    @property
    def row(self) -> tuple[object, ...]:
        return astuple(self)[:10]

    @property
    def line(self) -> str:
        return "|".join(str(field) for field in self.row)


def make_payment(
    world: World,
    rng: random.Random,
    day: date,
    sender: str,
    receiver: str,
    eur: float,
    payment_format: str,
    reference: str,
    *,
    received_currency: str = EURO,
    attempt_id: int | None = None,
    typology: str | None = None,
) -> Payment:
    amount = round(eur, 2)
    text = rng.choice(world.config.pools.references[reference])
    return Payment(
        datetime.combine(day, time(rng.randint(FIRST_PAYMENT_HOUR, LAST_PAYMENT_HOUR), rng.randint(0, 59))),
        sender,
        receiver,
        amount,
        EURO,
        round(amount / world.config.personas.fx_rates[received_currency], 2),
        received_currency,
        payment_format,
        amount,
        text.format(month=day.strftime("%B"), number=rng.randint(1000, 9999)),
        attempt_id,
        typology,
    )


class Balances:
    def __init__(self, opening: Mapping[str, float]) -> None:
        self.amounts = dict(opening)

    def affordable(self, account: str, eur: float) -> float:
        return min(eur, self.amounts.get(account, math.inf))

    def settle(self, payment: Payment) -> None:
        if payment.from_account == payment.to_account:
            return
        if payment.from_account in self.amounts:
            self.amounts[payment.from_account] -= payment.amount_paid_eur
        if payment.to_account in self.amounts:
            self.amounts[payment.to_account] += payment.amount_paid_eur


class Tape:
    def __init__(self, seed: int) -> None:
        self.seed = seed
        self.next_txn_id = 1
        self.digest = hashlib.sha256()

    @property
    def sha256(self) -> str:
        return self.digest.hexdigest()

    def order_key(self, payment: Payment) -> tuple[datetime, str]:
        text = f"{self.seed}|{payment.line}"
        return payment.ts, hashlib.sha256(text.encode()).hexdigest()

    def append(self, conn: psycopg.Connection[Any], payments: Iterable[Payment]) -> None:
        ordered = sorted(payments, key=self.order_key)
        numbered = list(enumerate(ordered, start=self.next_txn_id))
        self.next_txn_id += len(ordered)
        for txn_id, payment in numbered:
            self.digest.update(f"{txn_id}|{payment.line}\n".encode())
        transactions = [(txn_id, *payment.row) for txn_id, payment in numbered]
        labels = [(txn_id, payment.is_laundering) for txn_id, payment in numbered]
        attempts = [(p.attempt_id, p.typology, txn_id) for txn_id, p in numbered if p.is_laundering]
        copy_frame(conn, "core", "transactions", pl.DataFrame(transactions, schema=TRANSACTION_SCHEMA, orient="row"))
        copy_frame(conn, "truth", "transaction_labels", pl.DataFrame(labels, schema=LABEL_SCHEMA, orient="row"))
        copy_frame(conn, "truth", "pattern_attempts", pl.DataFrame(attempts, schema=ATTEMPT_SCHEMA, orient="row"))
