# PPFI source spike

Date: 2026-09-27  
Branch: `feat/ppfi-source-spike`

## Question-led reason to add this source

The Priority Places for Food Index (PPFI) enables Soundings to move beyond
"where are the food banks?" into questions about *where food-access
vulnerability is concentrated, why, and how that lines up with provision*.

The spike is justified by four evaluation questions:

1. Which neighbourhoods in Middlesbrough are most vulnerable to difficulties
   accessing affordable food?
2. Why is a particular neighbourhood a priority place for food?
3. Where are there high-priority food neighbourhoods but relatively little
   food-bank provision?
4. What civil-society organisations in high-priority areas may already be
   responding?

## Source

Primary source: HASP Priority Places for Food Index v2.1, DOI 10.82147/003.

HASP describes PPFI as a static July 2024 composite index built from seven
domains. The primary v2.1 dataset covers Great Britain. Rankings are produced
within country, so cross-country comparisons are not valid.

The Tier-0 catalogue download currently sits behind HASP's MetadataWorks
download flow. For this spike, Soundings uses a pinned copy of the England LSOA
PPFI deciles from HASP's official PPFI-IMD Explorer repository:

- repository: `Leeds-HASP/imd_ppfi_dash_app`
- commit: `63424725c7db67dd3c1546572430a54ea5f9550f`
- file: `data/ppfi_imd_lsoa_england.geojson`

Only feature properties are loaded; geometry is discarded and `LSOA21CD`
joins to Soundings' existing place spine.

## Indicators

The source adds the overall PPFI decile plus seven domain deciles:

- `food.ppfi.overall_decile`
- `food.ppfi.supermarket_proximity_decile`
- `food.ppfi.supermarket_accessibility_decile`
- `food.ppfi.ecommerce_access_decile`
- `food.ppfi.nonsupermarket_proximity_decile`
- `food.ppfi.socioeconomic_barriers_decile`
- `food.ppfi.family_food_support_decile`
- `food.ppfi.fuel_poverty_decile`

For all of these, **1 = highest priority / most vulnerable** and **10 = lowest
priority**.

## Deliberate limitations of this spike

- Current ingestion is England LSOA only, because that is the openly reachable
  official mirror we can pin and test without an authenticated data download.
- Do not describe PPFI as current food insecurity. It is a static composite
  index with constituent data from several vintages.
- Do not compare country-relative deciles directly between England, Scotland and Wales.
- The loader stores PPFI's published deciles rather than attempting to
  reconstruct the underlying index.
- No LTLA aggregate is fabricated. Neighbourhood questions use
  `get_sub_areas`; parent values are allowed to remain absent.

## Follow-up before calling this complete UK coverage

Obtain a stable machine-readable primary HASP download URL (or supported API
access) for dataset 5276, inspect the full geography/code contract, then extend
the loader to Wales and Scotland. Keep country-relative ranking semantics intact
rather than creating a Great Britain-wide pseudo-rank.
