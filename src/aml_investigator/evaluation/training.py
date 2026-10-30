import psycopg

from aml_investigator.core.sql import load
from aml_investigator.monitoring.calibration import train_split

TRAIN_LABELS_SQL = load(__package__, "train_labels")


def read_train_labels(evaluator_conninfo: str) -> dict[int, bool]:
    with psycopg.connect(evaluator_conninfo) as conn:
        train = train_split(conn)["train"]
        rows = conn.execute(TRAIN_LABELS_SQL, {"first_day": train.first, "last_day": train.last}).fetchall()
    return {int(alert_id): bool(escalate) for alert_id, escalate in rows}
