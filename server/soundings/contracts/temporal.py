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
_FINANCIAL_YEAR = re.compile(r"^(\d{4})/(\d{2}|\d{4})$")


def parse_period(label: str) -> TemporalExtent:
    """Parse common Soundings period labels without inventing false precision."""
    value = label.strip()

    if match := _DAY.fullmatch(value):
        try:
            observed = date(int(match[1]), int(match[2]), int(match[3]))
        except ValueError:
            return _unknown(value)
        return TemporalExtent(
            label=value,
            granularity="day",
            reference_start=observed,
            reference_end=observed,
        )

    if match := _MONTH.fullmatch(value):
        year, month = int(match[1]), int(match[2])
        if month < 1 or month > 12:
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
        return TemporalExtent(
            label=value,
            granularity="year",
            reference_start=date(year, 1, 1),
            reference_end=date(year, 12, 31),
        )

    quarter_match = _QUARTER.fullmatch(value)
    if quarter_match:
        year, quarter = int(quarter_match[1]), int(quarter_match[2])
        return _quarter_extent(value, year, quarter)

    quarter_alt_match = _QUARTER_ALT.fullmatch(value)
    if quarter_alt_match:
        quarter, year = int(quarter_alt_match[1]), int(quarter_alt_match[2])
        return _quarter_extent(value, year, quarter)

    if match := _FINANCIAL_YEAR.fullmatch(value):
        start_year = int(match[1])
        end_text = match[2]
        end_year = int(end_text) if len(end_text) == 4 else (start_year // 100 * 100) + int(end_text)
        if end_year < start_year:
            end_year += 100
        if end_year != start_year + 1:
            return _unknown(value)
        return TemporalExtent(
            label=value,
            granularity="financial_year",
            reference_start=date(start_year, 4, 1),
            reference_end=date(end_year, 3, 31),
        )

    return _unknown(value)


def period_sort_key(label: str) -> tuple[int, int, str]:
    """Chronological key for known periods, lexical fallback for unknown ones."""
    extent = parse_period(label)
    if extent.reference_start is not None:
        return (0, extent.reference_start.toordinal(), extent.label)
    return (1, 0, extent.label)


def period_in_window(label: str, period_from: str | None, period_to: str | None) -> bool:
    """Inclusive period window using structured dates when both sides are known."""
    if period_from and _compare_periods(label, period_from) < 0:
        return False
    if period_to and _compare_periods(label, period_to) > 0:
        return False
    return True


def _compare_periods(left: str, right: str) -> int:
    left_extent = parse_period(left)
    right_extent = parse_period(right)
    if left_extent.reference_start is not None and right_extent.reference_start is not None:
        left_date = left_extent.reference_start
        right_date = right_extent.reference_start
        return (left_date > right_date) - (left_date < right_date)
    return (left > right) - (left < right)


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
