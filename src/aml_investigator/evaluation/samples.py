import json
import random
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any, Literal

import psycopg

from aml_investigator.core.settings import Settings
from aml_investigator.core.sql import load
from aml_investigator.data.splits import DayRange
from aml_investigator.evaluation.openings import record_opening, require_reason
from aml_investigator.monitoring.calibration import train_split

EvalSplit = Literal["val", "test"]

DEFAULT_SIZES: dict[EvalSplit, int] = {"val": 200, "test": 400}
POSITIVE_SHARE = 1 / 3
SAMPLES_DIR = Path("eval/samples")

SPLIT_POPULATION_SQL = load(__package__, "split_population")


@dataclass(frozen=True, order=True)
class AlertKey:
    account_id: str
    alert_date: date


@dataclass(frozen=True)
class Strata:
    positive: int
    negative: int

    def by_truth(self) -> dict[bool, int]:
        return {True: self.positive, False: self.negative}


@dataclass(frozen=True)
class Sample:
    database: str
    split: EvalSplit
    seed: int
    created_at: str
    days: DayRange
    population: Strata
    drawn: Strata
    keys: tuple[AlertKey, ...]


@dataclass(frozen=True)
class LabelledPopulation:
    positives: list[AlertKey]
    negatives: list[AlertKey]

    def strata(self) -> Strata:
        return Strata(len(self.positives), len(self.negatives))

    def sorted_keys(self) -> list[AlertKey]:
        return sorted(self.positives + self.negatives)


def sample_path(workspace: Path, database: str, split: EvalSplit) -> Path:
    return workspace / SAMPLES_DIR / database / f"{split}.json"


def create_sample(
    settings: Settings,
    *,
    split: EvalSplit,
    size: int | None,
    seed: int,
    test_reason: str | None,
    workspace: Path = Path(),
) -> Path:
    require_reason(split, test_reason)
    path = sample_path(workspace, settings.database, split)
    if path.exists():
        raise FileExistsError(f"{path} exists; samples are frozen")
    with psycopg.connect(settings.evaluator_conninfo) as conn:
        days = train_split(conn)[split]
        population = labelled_population(conn, days)
    if test_reason is not None:
        record_test_sampling(workspace, settings.database, population.strata(), test_reason)
    sample = draw(population, size=size or DEFAULT_SIZES[split], seed=seed)
    write_sample(path, settings.database, split, seed, days, population.strata(), sample)
    return path


def labelled_population(conn: psycopg.Connection[Any], days: DayRange) -> LabelledPopulation:
    rows = conn.execute(SPLIT_POPULATION_SQL, {"first": days.first, "last": days.last}).fetchall()
    unlabelled = sum(1 for *_, escalate_truth in rows if escalate_truth is None)
    if unlabelled:
        raise ValueError(f"{unlabelled} alerts in {days.first}..{days.last} have no truth; run `aml truth alerts`")
    return LabelledPopulation(
        positives=[
            AlertKey(account_id, alert_date) for account_id, alert_date, escalate_truth in rows if escalate_truth
        ],
        negatives=[
            AlertKey(account_id, alert_date) for account_id, alert_date, escalate_truth in rows if not escalate_truth
        ],
    )


def draw(population: LabelledPopulation, *, size: int, seed: int) -> LabelledPopulation:
    rng = random.Random(seed)  # noqa: S311
    positive_count = min(round(size * POSITIVE_SHARE), len(population.positives))
    negative_count = min(size - positive_count, len(population.negatives))
    return LabelledPopulation(
        positives=rng.sample(population.positives, positive_count),
        negatives=rng.sample(population.negatives, negative_count),
    )


def record_test_sampling(workspace: Path, database: str, strata: Strata, reason: str) -> None:
    record_opening(
        workspace,
        computed=(
            f"{database}: test sample drawn; strata counts used for stratification: "
            f"{strata.positive} positive and {strata.negative} negative alerts."
        ),
        reason=reason,
    )


def write_sample(
    path: Path,
    database: str,
    split: EvalSplit,
    seed: int,
    days: DayRange,
    population: Strata,
    drawn: LabelledPopulation,
) -> None:
    document = {
        "database": database,
        "split": split,
        "seed": seed,
        "created_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "days": {"first": days.first.isoformat(), "last": days.last.isoformat()},
        "population": strata_document(population),
        "drawn": strata_document(drawn.strata()),
        "alerts": [
            {"account_id": key.account_id, "alert_date": key.alert_date.isoformat()} for key in drawn.sorted_keys()
        ],
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(document, indent=2) + "\n")


def strata_document(strata: Strata) -> dict[str, int]:
    return {"positive": strata.positive, "negative": strata.negative}


def load_sample(path: Path) -> Sample:
    document = json.loads(path.read_text())
    return Sample(
        database=document["database"],
        split=document["split"],
        seed=document["seed"],
        created_at=document["created_at"],
        days=DayRange(date.fromisoformat(document["days"]["first"]), date.fromisoformat(document["days"]["last"])),
        population=Strata(**document["population"]),
        drawn=Strata(**document["drawn"]),
        keys=tuple(
            AlertKey(alert["account_id"], date.fromisoformat(alert["alert_date"])) for alert in document["alerts"]
        ),
    )
