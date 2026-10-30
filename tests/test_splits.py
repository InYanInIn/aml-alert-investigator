from datetime import date

import pytest

from aml_investigator.splits import DayRange, day_splits


def test_ten_days_split_six_two_two() -> None:
    assert day_splits(date(2022, 9, 1), date(2022, 9, 10)) == {
        "train": DayRange(date(2022, 9, 1), date(2022, 9, 6)),
        "val": DayRange(date(2022, 9, 7), date(2022, 9, 8)),
        "test": DayRange(date(2022, 9, 9), date(2022, 9, 10)),
    }


def test_too_few_days_is_rejected() -> None:
    with pytest.raises(ValueError, match="too few"):
        day_splits(date(2022, 9, 1), date(2022, 9, 2))
