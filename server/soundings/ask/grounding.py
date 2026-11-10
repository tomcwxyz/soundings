"""Check that the figures in an answer came from a tool result.

Every number a tool returns during an Ask run is collected into an
``EvidenceSet``. When the model composes its answer, each figure in the
prose and in model-typed chart values is matched against that set, allowing
for the rounding the figure was displayed with and for proportions shown as
percentages. A figure with no match is "untraced": the model worked it out
itself or remembered it, which is exactly what the 9 November 2026 baseline
found behind most of its grounding and synthesis failures.

The check is deliberately conservative about what counts as a figure. Years,
period labels, geography codes, ordinals and small counts ("the 3 most
deprived") are ignored, because flagging them would teach the model to
ignore the check.
"""

from __future__ import annotations

import bisect
import re
from dataclasses import dataclass, field
from typing import Any

# A number, optionally with thousands separators and decimals, then an
# optional scale or percent suffix. The look-behind keeps codes (E01011954),
# quarter labels (Q1) and period halves (2019/20) out.
_FIGURE = re.compile(
    r"(?<![\w./-])"
    r"(?P<sign>[+\-\u2212]?)"
    r"(?P<int>\d{1,3}(?:,\d{3})+|\d+)"
    r"(?:\.(?P<dec>\d+))?"
    r"(?P<ord>st|nd|rd|th)?"
    r"(?:\s*(?P<suffix>%|per\s?cent\b|percentage\s+points?\b|pp\b|million\b|bn\b|billion\b))?",
    re.IGNORECASE,
)
_SCALES = {"million": 1_000_000.0, "bn": 1_000_000_000.0, "billion": 1_000_000_000.0}
_SMALL_COUNT_MAX = 10
_YEAR_RANGE = (1900, 2100)
# Some small integers are substantive counts, not list numbers or ranks.
_COUNT_NOUN = re.compile(
    r"^(?:people|residents|households|children|pupils|schools|charities|"
    r"organisations|jobs|wards|lsoas|neighbourhoods|sub-areas|cases)\b",
    re.IGNORECASE,
)
# Source metadata, coordinates, paging controls and geometry are not observations.
_NON_EVIDENCE_KEYS = frozenset(
    {
        "lat",
        "lon",
        "lng",
        "latitude",
        "longitude",
        "coordinates",
        "geometry",
        "bbox",
        "bounding_box",
        "limit",
        "offset",
        "page",
        "page_size",
        "total_pages",
        "retrieved_at",
    }
)


@dataclass(frozen=True)
class Figure:
    """A number as it appears in the answer."""

    text: str
    value: float
    is_percent: bool
    # Precision shown: 1 for "32.4", 0 for "546,412", -3 for "546,000",
    # -5 for "1.2 million". Tolerance is half a unit in that place.
    decimals: int
    points: bool = False

    @property
    def tolerance(self) -> float:
        return 0.5 * 10.0 ** (-self.decimals)


def extract_figures(markdown: str) -> list[Figure]:
    figures: list[Figure] = []
    for m in _FIGURE.finditer(markdown):
        if m.group("ord"):
            continue
        int_part = m.group("int")
        dec_part = m.group("dec") or ""
        suffix = (m.group("suffix") or "").lower()
        is_percent = suffix in ("%",) or suffix.startswith("per")
        points = suffix.startswith("percentage point") or suffix == "pp"
        if points:
            is_percent = False
        scale = _SCALES.get(suffix, 1.0)
        value = float(int_part.replace(",", "") + ("." + dec_part if dec_part else ""))
        if m.group("sign") in ("-", "\u2212"):
            value = -value

        bare_integer = not dec_part and not suffix
        if bare_integer and "," not in int_part:
            if _YEAR_RANGE[0] <= abs(value) <= _YEAR_RANGE[1]:
                continue
            if abs(value) <= _SMALL_COUNT_MAX and not _COUNT_NOUN.match(
                markdown[m.end() :].lstrip()
            ):
                continue

        if dec_part:
            decimals = len(dec_part)
        else:
            digits = int_part.replace(",", "")
            trailing = len(digits) - len(digits.rstrip("0")) if digits.strip("0") else 0
            decimals = -trailing if "," in int_part else 0
        if scale != 1.0:
            decimals -= len(str(int(scale))) - 1
            value *= scale

        figures.append(
            Figure(
                text=m.group(0).strip(),
                value=value,
                is_percent=is_percent,
                decimals=decimals,
                points=points,
            )
        )
    return figures


@dataclass(frozen=True)
class EvidenceFact:
    """A tool-returned value, bound to a measure, place and reference period."""

    value: float
    indicator_key: str | None = None
    place_id: str | None = None
    period: str | None = None
    ref_id: str | None = None
    unit: str | None = None
    source_id: str | None = None


@dataclass
class EvidenceSet:
    """Every number returned by a tool during one Ask run, including context."""

    values: set[float] = field(default_factory=set)
    facts: list[EvidenceFact] = field(default_factory=list)
    reference_facts: dict[str, EvidenceFact] = field(default_factory=dict)
    _reference_keys: set[tuple[str, str, str | None, float, str | None]] = field(
        default_factory=set, repr=False
    )
    _sorted: list[float] | None = field(default=None, repr=False)

    def add_result(self, result: Any) -> None:
        # Sub-area tools return one shared source at the top level; their
        # individual rows do not repeat it. Inherit it only when unambiguous.
        source_id: str | None = None
        if isinstance(result, dict):
            sources = result.get("sources")
            if isinstance(sources, list) and len(sources) == 1 and isinstance(sources[0], dict):
                candidate = sources[0].get("source_id")
                if isinstance(candidate, str):
                    source_id = candidate
        self._walk(result, source_id=source_id)
        self._sorted = None

    def add_value(self, value: float) -> None:
        self.values.add(float(value))
        self._sorted = None

    def _walk(
        self,
        node: Any,
        indicator_key: str | None = None,
        place_id: str | None = None,
        period: str | None = None,
        unit: str | None = None,
        source_id: str | None = None,
        value_key: str | None = None,
    ) -> None:
        if isinstance(node, bool):
            return
        if isinstance(node, (int, float)):
            self.values.add(float(node))
            self.facts.append(EvidenceFact(float(node), indicator_key, place_id, period))
            if (
                value_key in ("value", "parent_value")
                and indicator_key is not None
                and place_id is not None
                and len(self.reference_facts) < 1500
            ):
                key = (indicator_key, place_id, period, float(node), source_id)
                if key not in self._reference_keys:
                    self._reference_keys.add(key)
                    ref_id = f"obs:{len(self.reference_facts) + 1}"
                    self.reference_facts[ref_id] = EvidenceFact(
                        float(node),
                        indicator_key,
                        place_id,
                        period,
                        ref_id,
                        unit,
                        source_id,
                    )
        elif isinstance(node, dict):
            candidate_indicator = node.get("indicator_key") or node.get("indicator")
            candidate_place = node.get("place_id")
            candidate_period = node.get("period")
            candidate_unit = node.get("unit")
            candidate_source = node.get("source_id")
            if not isinstance(candidate_source, str):
                source = node.get("source")
                candidate_source = source.get("source_id") if isinstance(source, dict) else None
            next_unit = candidate_unit if isinstance(candidate_unit, str) else unit
            next_source = candidate_source if isinstance(candidate_source, str) else source_id
            next_indicator = (
                candidate_indicator if isinstance(candidate_indicator, str) else indicator_key
            )
            next_place = candidate_place if isinstance(candidate_place, str) else place_id
            next_period = candidate_period if isinstance(candidate_period, str) else period
            for key, child in node.items():
                if key in _NON_EVIDENCE_KEYS:
                    continue
                if key == "parent_value" and isinstance(node.get("parent_place_id"), str):
                    self._walk(
                        child,
                        next_indicator,
                        node["parent_place_id"],
                        next_period,
                        next_unit,
                        next_source,
                        key,
                    )
                else:
                    self._walk(
                        child,
                        next_indicator,
                        next_place,
                        next_period,
                        next_unit,
                        next_source,
                        key,
                    )
        elif isinstance(node, (list, tuple)):
            for child in node:
                self._walk(child, indicator_key, place_id, period, unit, source_id)

    def _near(self, target: float, tolerance: float) -> bool:
        if self._sorted is None:
            self._sorted = sorted(self.values)
        tol = tolerance + abs(target) * 1e-9
        i = bisect.bisect_left(self._sorted, target - tol)
        return i < len(self._sorted) and self._sorted[i] <= target + tol

    def supports(self, value: float, *, is_percent: bool, decimals: int) -> bool:
        tol = 0.5 * 10.0 ** (-decimals)
        if self._near(value, tol):
            return True
        # A percentage may come from a proportion: 32.4% <- 0.324.
        return is_percent and self._near(value / 100.0, tol / 100.0)

    def supports_in_context(
        self,
        value: float,
        *,
        indicator_key: str,
        place_id: str,
        period: str | None = None,
        is_percent: bool = False,
        decimals: int = 2,
    ) -> bool:
        """Only a matching indicator/place/period can support a typed chart row.

        Narrative remains weaker until each claim carries an explicit fact ID.
        """
        tolerance = 0.5 * 10.0 ** (-decimals)
        for fact in self.reference_facts.values():
            if fact.indicator_key != indicator_key or fact.place_id != place_id:
                continue
            if period and fact.period != period:
                continue
            if abs(fact.value - value) <= tolerance + abs(value) * 1e-9:
                return True
            if is_percent and abs(fact.value - value / 100) <= (
                tolerance / 100 + abs(value) * 1e-11
            ):
                return True
        return False

    def reference_metadata(self, after: int = 0, limit: int = 80) -> list[dict[str, Any]]:
        """Compact IDs and provenance for the model, without duplicating entire tool payloads."""
        return [
            {
                "ref_id": ref_id,
                "indicator_key": fact.indicator_key,
                "place_id": fact.place_id,
                "period": fact.period,
                "value": fact.value,
                "unit": fact.unit,
                "source_id": fact.source_id,
            }
            for ref_id, fact in list(self.reference_facts.items())[after : after + limit]
        ]

    def traces_with_refs(self, figure: Figure, ref_ids: list[str]) -> bool:
        """A cited figure must match the cited observation, not an arbitrary tool number."""
        for ref_id in ref_ids:
            fact = self.reference_facts.get(ref_id)
            if fact is None:
                continue
            tolerance = figure.tolerance + abs(figure.value) * 1e-9
            if abs(fact.value - figure.value) <= tolerance:
                return True
            if figure.is_percent and abs(fact.value - figure.value / 100) <= (tolerance / 100):
                return True
        return False

    def traces(self, figure: Figure) -> bool:
        return self.supports(
            figure.value,
            is_percent=figure.is_percent,
            decimals=figure.decimals,
        )

    def untraced(self, markdown: str) -> list[Figure]:
        return [f for f in extract_figures(markdown) if not self.traces(f)]


def shown_decimals(value: float) -> int:
    """The precision a bare float carries: 0.324 -> 3, 546000.0 -> -3."""
    text_value = repr(float(value))
    if "e" in text_value:
        return 6
    whole, _, frac = text_value.partition(".")
    frac = frac.rstrip("0")
    if frac:
        return len(frac)
    digits = whole.lstrip("-")
    return -(len(digits) - len(digits.rstrip("0"))) if digits.strip("0") else 0
