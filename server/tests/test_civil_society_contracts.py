"""Contract round-trip + invariants for CivilSocietyProfile."""

from datetime import UTC, datetime

import pytest

from soundings.contracts.civil_society import (
    CivilSocietyProfile,
    IncomeBucket,
    RegistrationCohort,
)
from soundings.contracts.source_ref import SourceRef


def _src() -> SourceRef:
    return SourceRef(
        source_id="charity_commission",
        source_label="Charity Commission for England and Wales",
        publisher="Charity Commission",
        retrieved_at=datetime.now(tz=UTC),
        cache_status="cached",
        licence="https://www.nationalarchives.gov.uk/doc/open-government-licence/version/3/",
    )


def test_profile_round_trips_through_json() -> None:
    profile = CivilSocietyProfile(
        place_id="ltla24:E06000047",
        total_organisations=1034,
        registered_address_count=900,
        with_reported_income=812,
        median_income=42000.0,
        mean_income=187000.0,
        income_buckets=[
            IncomeBucket(label="<10k", lower=0, upper=10_000, count=312),
            IncomeBucket(label="10k-100k", lower=10_000, upper=100_000, count=305),
            IncomeBucket(label="100k-1m", lower=100_000, upper=1_000_000, count=160),
            IncomeBucket(label="1m-10m", lower=1_000_000, upper=10_000_000, count=29),
            IncomeBucket(label="10m+", lower=10_000_000, upper=None, count=6),
        ],
        registration_cohort=[
            RegistrationCohort(year=2020, registered=22, removed=8, net=14),
            RegistrationCohort(year=2021, registered=29, removed=10, net=19),
        ],
        sources=[_src()],
        caveats=["Income from latest CC annual return; 222 charities have no return on file."],
        partial=False,
    )
    blob = profile.model_dump_json()
    rehydrated = CivilSocietyProfile.model_validate_json(blob)
    assert rehydrated == profile


def test_income_bucket_label_invariant() -> None:
    # The top bucket has no upper bound.
    bucket = IncomeBucket(label="10m+", lower=10_000_000, upper=None, count=6)
    assert bucket.upper is None

    # Lower-bound bucket has an upper.
    bucket2 = IncomeBucket(label="<10k", lower=0, upper=10_000, count=312)
    assert bucket2.upper == 10_000


def test_cohort_net_invariant() -> None:
    cohort = RegistrationCohort(year=2024, registered=10, removed=3, net=7)
    assert cohort.net == cohort.registered - cohort.removed
    with pytest.raises(ValueError):
        RegistrationCohort(year=2024, registered=10, removed=3, net=999)
