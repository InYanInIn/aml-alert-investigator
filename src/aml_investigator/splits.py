from dataclasses import dataclass
from datetime import date, timedelta
from typing import Literal

Split = Literal["train", "val", "test"]


@dataclass(frozen=True)
class DayRange:
    first: date
    last: date


def day_splits(first_day: date, last_day: date) -> dict[Split, DayRange]:
    total = (last_day - first_day).days + 1
    train_days = round(total * 0.6)
    val_days = round(total * 0.2)
    test_days = total - train_days - val_days
    if min(train_days, val_days, test_days) < 1:
        raise ValueError(f"{total} days are too few for a 60/20/20 split")
    val_first = first_day + timedelta(days=train_days)
    test_first = val_first + timedelta(days=val_days)
    return {
        "train": DayRange(first_day, val_first - timedelta(days=1)),
        "val": DayRange(val_first, test_first - timedelta(days=1)),
        "test": DayRange(test_first, last_day),
    }
