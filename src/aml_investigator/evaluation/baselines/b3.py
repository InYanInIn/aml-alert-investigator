from collections.abc import AsyncIterator, Mapping, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass

import numpy as np
import numpy.typing as npt
from sklearn.ensemble import HistGradientBoostingClassifier  # type: ignore[import-untyped]

from aml_investigator.core.db import Pool, open_read_only_pool
from aml_investigator.core.sql import load
from aml_investigator.evaluation.systems import EvalAlert, SystemDecision

RANDOM_STATE = 0
ALERT_FEATURES_SQL = load(__package__, "alert_features")


@dataclass(frozen=True)
class FeatureMatrix:
    alert_ids: tuple[int, ...]
    names: tuple[str, ...]
    values: npt.NDArray[np.float64]


@dataclass(frozen=True)
class TrainingSummary:
    alerts: int
    positives: int


@dataclass(frozen=True)
class GradientBoosting:
    pool: Pool
    model: HistGradientBoostingClassifier
    training_summary: TrainingSummary
    excluded_features: frozenset[str]
    name: str

    async def decide(self, alert: EvalAlert) -> SystemDecision:
        features = without(await alert_features(self.pool, [alert.alert_id]), self.excluded_features)
        [probability] = self.model.predict_proba(features.values)[:, 1]
        return SystemDecision(recommendation=None, risk_score=float(probability))


async def alert_features(pool: Pool, alert_ids: Sequence[int]) -> FeatureMatrix:
    async with pool.connection() as conn:
        cursor = await conn.execute(ALERT_FEATURES_SQL, {"alert_ids": list(alert_ids)})
        rows = await cursor.fetchall()
        names = tuple(column.name for column in (cursor.description or ())[1:])
    values = np.array([[float(row[name]) for name in names] for row in rows], dtype=np.float64)
    return FeatureMatrix(tuple(int(row["alert_id"]) for row in rows), names, values.reshape(len(rows), len(names)))


def without(features: FeatureMatrix, excluded: frozenset[str]) -> FeatureMatrix:
    unknown = excluded - set(features.names)
    if unknown:
        raise ValueError(f"unknown features: {', '.join(sorted(unknown))}")
    kept = [index for index, name in enumerate(features.names) if name not in excluded]
    return FeatureMatrix(features.alert_ids, tuple(features.names[index] for index in kept), features.values[:, kept])


def fit(features: FeatureMatrix, labels: Mapping[int, bool]) -> HistGradientBoostingClassifier:
    targets = np.array([labels[alert_id] for alert_id in features.alert_ids], dtype=np.int64)
    return HistGradientBoostingClassifier(random_state=RANDOM_STATE).fit(features.values, targets)


@asynccontextmanager
async def open_gradient_boosting(
    app_conninfo: str,
    train_labels: Mapping[int, bool],
    *,
    name: str = "b3",
    excluded_features: frozenset[str] = frozenset(),
) -> AsyncIterator[GradientBoosting]:
    async with open_read_only_pool(app_conninfo) as pool:
        features = without(await alert_features(pool, sorted(train_labels)), excluded_features)
        model = fit(features, train_labels)
        positives = sum(train_labels[alert_id] for alert_id in features.alert_ids)
        summary = TrainingSummary(len(features.alert_ids), positives)
        yield GradientBoosting(pool, model, summary, excluded_features, name)
