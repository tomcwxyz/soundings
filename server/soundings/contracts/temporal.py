"""Shared temporal semantics for source period labels.

Soundings keeps the source-facing ``period`` string for compatibility and
provenance, but consumers should not have to treat that string as the whole
time model. This module conservatively derives a structured extent for common
period labels and leaves unfamiliar labels explicitly unknown.
"""

import calendar
import re
from datetime import date
from typing import Literal

from pydantic import BaseModel

TemporalGranularity = Literal[
    "day",
    "month",
    "quarter",
    "year",
    "financial_year",
    "range",
    "edition",
    "unknown",
]


class TemporalExtent(BaseModel):
    label: str
    granularity: TemporalGranularity
    reference_start: date | None = None
    reference_end: date | None = None
    edition: str | None = None


_YEAR = re.compile(r"^(\d{4})$")
_MONTH = re.compile(r"^(\d{4})-(\d{2})$")
_DAY = re.compile(r"^(\d{4})-(\d{2})-(\d{2})$")
_QUARTER = re.compile(r"^(\d{4})-Q([1-4])$", re.IGNORECASE)
_QUARTER_ALT = re.compile(r"^Q([1-4])\s+(\d{4})$", re.IGNORECASE)
_YEAR_RANGE = re.compile(r"^(\d{4})/(\d{2}|\d{4})$")
_FINANCIAL_YEAR = re.compile(r"^FY\s*(\d{4})/(\d{2}|\d{4})$", re.IGNORECASE)


def parse_period(label: str) -> TemporalExtent:
    """Parse common Soundings period labels without inventing false precision."""
    value = label.strip()

    if match := _DAY.fullmatch(value):
        observed = _safe_date(int(match[1]), int(match[2]), int(match[3]))
        if observed is None:
            return _unknown(value)
        return TemporalExtent(
            label=value,
            granularity="day",
            reference_start=observed,
            reference_end=observed,
        )

    if match := _MONTH.fullmatch(value):
        year, month = int(match[1]), int(match[2])
        if not _valid_year(year) or month < 1 or month > 12:
            return _unknown(value)
        end_day = calendar.monthrange(year, month)[1]
        return TemporalExtent(
            label=value,
            granularity="month",
            reference_start=date(year, month, 1),
            reference_end=date(year, month, end_day),
        )

    if match := _YEAR.fullmatch(value):
        year = int(match[1])
        if not _valid_year(year):
            return _unknown(value)
        return TemporalExtent(
            label=value,
            granularity="year",
            reference_start=date(year, 1, 1),
            reference_end=date(year, 12, 31),
        )

    quarter_match = _QUARTER.fullmatch(value)
    if quarter_match:
        year, quarter = int(quarter_match[1]), int(quarter_match[2])
        if not _valid_year(year):
            return _unknown(value)
        return _quarter_extent(value, year, quarter)

    quarter_alt_match = _QUARTER_ALT.fullmatch(value)
    if quarter_alt_match:
        quarter, year = int(quarter_alt_match[1]), int(quarter_alt_match[2])
        if not _valid_year(year):
            return _unknown(value)
        return _quarter_extent(value, year, quarter)

    if match := _FINANCIAL_YEAR.fullmatch(value):
        start_year = int(match[1])
        end_year = _resolve_two_or_four_digit_year(start_year, match[2])
        if not _valid_year(start_year) or not _valid_year(end_year):
            return _unknown(value)
        if end_year != start_year + 1:
            return _unknown(value)
        return TemporalExtent(
            label=value,
            granularity="financial_year",
            reference_start=date(start_year, 4, 1),
            reference_end=date(end_year, 3, 31),
        )

    if match := _YEAR_RANGE.fullmatch(value):
        start_year = int(match[1])
        end_year = _resolve_two_or_four_digit_year(start_year, match[2])
        if not _valid_year(start_year) or not _valid_year(end_year):
            return _unknown(value)
        if end_year != start_year + 1:
            return _unknown(value)
        # A bare 2024/25 label might be an academic, financial or other
        # reporting year. Preserve it as a range rather than claiming dates.
        return TemporalExtent(label=value, granularity="range")

    return _unknown(value)


def period_sort_key(label: str) -> tuple[int, int, str]:
    """Sort known periods chronologically and keep unknown labels stable after them."""
    extent = parse_period(label)
    if extent.reference_start is not None:
        return (0, extent.reference_start.toordinal(), extent.label)
    return (1, 0, extent.label)


def period_in_window(label: str, period_from: str | None, period_to: str | None) -> bool:
    """Inclusive period window, excluding comparisons whose chronology is unknown."""
    if period_from:
        comparison = _compare_periods(label, period_from)
        if comparison is None or comparison < 0:
            return False
    if period_to:
        comparison = _compare_periods(label, period_to)
        if comparison is None or comparison > 0:
            return False
    return True


def _compare_periods(left: str, right: str) -> int | None:
    left_extent = parse_period(left)
    right_extent = parse_period(right)
    if left_extent.reference_start is None or right_extent.reference_start is None:
        return None
    left_date = left_extent.reference_start
    right_date = right_extent.reference_start
    return (left_date > right_date) - (left_date < right_date)


def _valid_year(year: int) -> bool:
    return date.min.year <= year <= date.max.year


def _safe_date(year: int, month: int, day: int) -> date | None:
    try:
        return date(year, month, day)
    except ValueError:
        return None


def _resolve_two_or_four_digit_year(start_year: int, end_text: str) -> int:
    if len(end_text) == 4:
        return int(end_text)
    end_year = (start_year // 100 * 100) + int(end_text)
    return end_year + 100 if end_year < start_year else end_year


def _quarter_extent(label: str, year: int, quarter: int) -> TemporalExtent:
    start_month = 1 + (quarter - 1) * 3
    end_month = start_month + 2
    end_day = calendar.monthrange(year, end_month)[1]
    return TemporalExtent(
        label=label,
        granularity="quarter",
        reference_start=date(year, start_month, 1),
        reference_end=date(year, end_month, end_day),
    )


def _unknown(label: str) -> TemporalExtent:
    return TemporalExtent(label=label, granularity="unknown")
