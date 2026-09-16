import pytest

from soundings.db.engine import get_engine
from soundings.loader import run as loader_run

pytestmark = pytest.mark.integration


async def test_combined_geography_refresh_reuses_one_chd_archive(monkeypatch) -> None:
    events: list[str] = []
    blob = b"same-chd-archive"

    class CurrentLoader:
        def __init__(self, *args, **kwargs) -> None:
            pass

        async def load(self) -> None:
            events.append(self.__class__.__name__)

    class PlacesLoader(CurrentLoader):
        pass

    class HierarchyLoader(CurrentLoader):
        pass

    class GeometriesLoader(CurrentLoader):
        pass

    class HistoricalPlacesLoader:
        def __init__(self, *args, **kwargs) -> None:
            pass

        async def load_from_zip_bytes(self, supplied: bytes) -> None:
            assert supplied is blob
            events.append("historical_places")

    class HistoricalHierarchyLoader:
        def __init__(self, *args, **kwargs) -> None:
            pass

        async def load_from_zip_bytes(self, supplied: bytes) -> None:
            assert supplied is blob
            events.append("historical_hierarchy")

    async def fake_fetch_chd_archive() -> bytes:
        events.append("fetch_chd")
        return blob

    monkeypatch.setattr(loader_run, "OnsGeographyPlacesLoader", PlacesLoader)
    monkeypatch.setattr(loader_run, "OnsGeographyHierarchyLoader", HierarchyLoader)
    monkeypatch.setattr(loader_run, "OnsGeographyGeometriesLoader", GeometriesLoader)
    monkeypatch.setattr(
        loader_run,
        "OnsGeographyHistoricalPlacesLoader",
        HistoricalPlacesLoader,
    )
    monkeypatch.setattr(
        loader_run,
        "OnsGeographyHistoricalHierarchyLoader",
        HistoricalHierarchyLoader,
    )
    monkeypatch.setattr(loader_run, "fetch_chd_archive", fake_fetch_chd_archive)

    geography_refresh = loader_run.build_source_registry(get_engine())["ons.geography"]
    await geography_refresh()

    assert events == [
        "PlacesLoader",
        "HierarchyLoader",
        "GeometriesLoader",
        "fetch_chd",
        "historical_places",
        "historical_hierarchy",
    ]
