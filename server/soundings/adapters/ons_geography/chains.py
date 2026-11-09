"""LookupChain configurations for the OGP hierarchy loader.

URLs and field names mirror ADR-0001 §Lookups. Treat any `(unverified)`
entry as needing a sanity-check at first run.
"""

from soundings.adapters.ons_geography.hierarchy_loader import LookupChain

OGP_LOOKUP_HOST = "https://services1.arcgis.com/ESMARspQHYMw9BZ9/arcgis/rest/services"

# LSOA21 → LTLA24 lookup. ONS publishes per-vintage variants
# (LSOA21_WD24_LAD24_EW_LU at the time of this pin); the legacy
# PCD-prefixed combined lookup no longer resolves.
LSOA_MSOA_LTLA = LookupChain(
    url=f"{OGP_LOOKUP_HOST}/LSOA21_WD24_LAD24_EW_LU/FeatureServer/0",
    levels=[
        ("lsoa21", "LSOA21CD"),
        ("ltla24", "LAD24CD"),
    ],
)

# The ward-based administrative lookup also supplies the full council chain.
# Repeated council rows are deduplicated by the hierarchy loader.
LTLA_UTLA_REGION_COUNTRY = LookupChain(
    url=f"{OGP_LOOKUP_HOST}/WD24_LAD24_CTYUA_RGN_CTRY_UK_LU/FeatureServer/0",
    levels=[
        ("ltla24", "LAD24CD"),
        ("utla24", "CTYUA24CD"),
        ("region", "RGN24CD"),
        ("country", "CTRY24CD"),
    ],
)

# Constituencies can cross council boundaries, and some wards are split.
# Do not turn this overlap lookup into constituency containment edges.
WARD_LTLA_UTLA = LookupChain(
    url=f"{OGP_LOOKUP_HOST}/WD24_PCON24_LAD24_UTLA24_UK_LU/FeatureServer/0",
    levels=[
        ("ward24", "WD24CD"),
        ("ltla24", "LAD24CD"),
        ("utla24", "UTLA24CD"),
    ],
)


ALL_CHAINS = [
    LSOA_MSOA_LTLA,
    LTLA_UTLA_REGION_COUNTRY,
    WARD_LTLA_UTLA,
]
