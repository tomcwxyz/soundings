from datetime import date

from soundings.contracts.temporal import (
    parse_period,
    period_in_window,
    period_sort_key,
)


def test_parse_annual_period() -> None:
    extent = parse_period("2024")
    assert extent.granularity == "year"
    assert extent.reference_start == date(2024, 1, 1)
    assert extent.reference_end == date(2024, 12, 31)


def test_parse_month_and_quarter_periods() -> None:
    month = parse_period("2024-02")
    quarter = parse_period("2024-Q2")
    assert month.reference_end == date(2024, 2, 29)
    assert quarter.reference_start == date(2024, 4, 1)
    assert quarter.reference_end == date(2024, 6, 30)


def test_parse_financial_year() -> None:
    extent = parse_period("2024/25")
    assert extent.granularity == "financial_year"
    assert extent.reference_start == date(2024, 4, 1)
    assert extent.reference_end == date(2025, 3, 31)


def test_unknown_period_keeps_label_without_false_dates() -> None:
    extent = parse_period("Census 2021 release")
    assert extent.label == "Census 2021 release"
    assert extent.granularity == "unknown"
    assert extent.reference_start is None
    assert extent.reference_end is None


def test_period_window_and_sort_use_structured_time() -> None:
    labels = ["2024-Q4", "2023-Q4", "2024-Q1"]
    assert sorted(labels, key=period_sort_key) == ["2023-Q4", "2024-Q1", "2024-Q4"]
    assert period_in_window("2024-Q2", "2024-Q1", "2024-Q3") is True
    assert period_in_window("2023-Q4", "2024-Q1", "2024-Q3") is False
