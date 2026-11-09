"""Pinned ONS Open Geography Portal feature service endpoints.

Mirror of the table in `docs/adr/0001-geography-data-sources.md`. If a URL
goes stale, update both files in lockstep.
"""

from dataclasses import dataclass

OGP_HOST = "https://services1.arcgis.com/ESMARspQHYMw9BZ9/arcgis/rest/services"


@dataclass(frozen=True)
class OgpLayer:
    place_type: str  # canonical Soundings type, e.g. "lsoa21"
    code_field: str  # ArcGIS attribute holding the canonical code
    name_field: str  # ArcGIS attribute holding the human name
    service_name: str  # ArcGIS service name; full URL is OGP_HOST/<name>/FeatureServer/0

    @property
    def feature_url(self) -> str:
        return f"{OGP_HOST}/{self.service_name}/FeatureServer/0"


# Boundary layers, smallest available variant. See ADR-0001 for fallback rationale.
BOUNDARY_LAYERS: dict[str, OgpLayer] = {
    "lsoa21": OgpLayer(
        place_type="lsoa21",
        code_field="LSOA21CD",
        name_field="LSOA21NM",
        # ONS appends a version suffix (`_V4` at the time of this pin); update
        # in lockstep with `docs/adr/0001-geography-data-sources.md` when ONS
        # publishes a new revision and the old URL 400s.
        service_name="Lower_layer_Super_Output_Areas_December_2021_Boundaries_EW_BSC_V4",
    ),
    "msoa21": OgpLayer(
        place_type="msoa21",
        code_field="MSOA21CD",
        name_field="MSOA21NM",
        service_name="Middle_layer_Super_Output_Areas_December_2021_Boundaries_EW_BGC_V3",
    ),
    "ltla24": OgpLayer(
        place_type="ltla24",
        code_field="LAD24CD",
        name_field="LAD24NM",
        service_name="Local_Authority_Districts_May_2024_Boundaries_UK_BUC",
    ),
    "utla24": OgpLayer(
        place_type="utla24",
        code_field="CTYUA24CD",
        name_field="CTYUA24NM",
        service_name="Counties_and_Unitary_Authorities_December_2024_Boundaries_UK_BUC",
    ),
    "region": OgpLayer(
        place_type="region",
        code_field="RGN24CD",
        name_field="RGN24NM",
        service_name="Regions_December_2024_Boundaries_EN_BGC",
    ),
    "country": OgpLayer(
        place_type="country",
        code_field="CTRY24CD",
        name_field="CTRY24NM",
        service_name="Countries_December_2024_Boundaries_UK_BUC",
    ),
    "westminster_constituency_24": OgpLayer(
        place_type="westminster_constituency_24",
        code_field="PCON24CD",
        name_field="PCON24NM",
        service_name="Westminster_Parliamentary_Constituencies_July_2024_Boundaries_UK_BUC",
    ),
    "ward24": OgpLayer(
        place_type="ward24",
        code_field="WD24CD",
        name_field="WD24NM",
        service_name="Wards_May_2024_Boundaries_UK_BSC",
    ),
}
