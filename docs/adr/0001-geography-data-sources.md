# ADR-0001: Geography data sources for the v1 spine

**Status:** Accepted
**Date:** 2026-05-10
**Context:** Phase 0 of v1 — orchestration & capture (`docs/plans/2026-05-05-soundings-v1-phase-0-plan.md`).

## Decision

The geography spine is loaded once at deploy time from the **ONS Open Geography Portal** (OGP) and the **ONS Code History Database** (CHD). The exact endpoints we consume are pinned below so a layer URL going stale becomes a single-file change in this ADR + the loader, not a hunt through code.

## Generalisation level — choosing BUC vs BGC vs BFC

OGP publishes each boundary at four generalisation levels:

- **BFC** — Full resolution, clipped to the coastline.
- **BFE** — Full resolution, extended to mean low water.
- **BGC** — Generalised, clipped to the coastline.
- **BSC** — Super-generalised, clipped to the coastline.
- **BUC** — Ultra-generalised, clipped to the coastline. ~6kb per polygon.

The plan asked for **BUC** everywhere because it is the smallest and we only need outlines for web display. **In practice, BUC is not published for every layer.** Where it is, we use it. Where it isn't, the fallback order is `BSC → BGC → BFC` — i.e. take the smallest available variant.

## Layers consumed

All Feature Services live on `services1.arcgis.com/ESMARspQHYMw9BZ9` unless otherwise noted. Each item below pairs a human-readable OGP page with the FeatureServer endpoint. Items marked `(unverified)` should be checked at first run and fixed here if they 404.

### Boundaries

| Layer | Edition | OGP page | FeatureServer endpoint |
|---|---|---|---|
| LSOA 2021 (England + Wales) | BSC (BUC not published) | [LSOA Dec 2021 BSC](https://geoportal.statistics.gov.uk/search?q=LSOA+2021+BSC) | `Lower_layer_Super_Output_Areas_December_2021_Boundaries_EW_BSC` *(unverified)* |
| MSOA 2021 (England + Wales) | BGC (BUC not published) | [MSOA Dec 2021 BGC](https://geoportal.statistics.gov.uk/search?q=MSOA+2021+BGC) | `Middle_layer_Super_Output_Areas_December_2021_Boundaries_EW_BGC_V3` (verified 9 November 2026; 7,264 areas) |
| LTLA 2024 (UK) | BUC (May 2024) | [LAD May 2024 BUC](https://geoportal.statistics.gov.uk/datasets/ons::local-authority-districts-may-2024-boundaries-uk-buc-2/about) | `Local_Authority_Districts_May_2024_Boundaries_UK_BUC` *(unverified)* |
| UTLA 2024 (UK) | BUC (December 2024) | [CTYUA Dec 2024 BUC](https://geoportal.statistics.gov.uk/datasets/ons::counties-and-unitary-authorities-december-2024-boundaries-uk-buc-2/explore) | `Counties_and_Unitary_Authorities_December_2024_Boundaries_UK_BUC` |
| Region 2024 (England) | BGC (BUC not published) | [Regions Dec 2024 BGC](https://geoportal.statistics.gov.uk/datasets/ons::regions-december-2024-boundaries-en-bgc-2/about) | `Regions_December_2024_Boundaries_EN_BGC` *(unverified)* |
| Country 2024 (UK) | BUC (verified 9 November 2026; full-resolution export timed out) | [Countries Dec 2024 BUC](https://services1.arcgis.com/ESMARspQHYMw9BZ9/arcgis/rest/services/Countries_December_2024_Boundaries_UK_BUC/FeatureServer/0) | `Countries_December_2024_Boundaries_UK_BUC` |
| Westminster Constituency 2024 (UK) | BUC (July 2024) | [WPC July 2024 BUC](https://geoportal.statistics.gov.uk/datasets/ons::westminster-parliamentary-constituencies-july-2024-boundaries-uk-buc-2/about) | `Westminster_Parliamentary_Constituencies_July_2024_Boundaries_UK_BUC` *(unverified)* |
| Ward 2024 (UK) | BSC (BUC not published) | [Wards May 2024 BSC](https://geoportal.statistics.gov.uk/datasets/b58c65bdad994ed3a33741eea7bb09ab_0/about) | `Wards_May_2024_Boundaries_UK_BSC` *(unverified)* |

The full URL pattern is:

```
https://services1.arcgis.com/ESMARspQHYMw9BZ9/arcgis/rest/services/{ServiceName}/FeatureServer/0
```

### Lookups

These are the join tables that drive `place_hierarchy`. CSVs hosted on OGP; we read them via the same arcgis service backend that powers the OGP item page.

| Active lookup | Edition | Verified ArcGIS service |
|---|---|---|
| LSOA → LTLA | 2021 LSOA / 2024 LAD | `LSOA21_WD24_LAD24_EW_LU` |
| LTLA → UTLA → Region → Country | 2024 | `WD24_LAD24_CTYUA_RGN_CTRY_UK_LU` |
| Ward → LTLA → UTLA | July 2024 | `WD24_PCON24_LAD24_UTLA24_UK_LU` |

These service names were verified on 9 November 2026 against the official ONS
ArcGIS directory above. The administrative lookup uses `CTYUA24CD`; the political
lookup calls that same level `UTLA24CD`. Duplicate rows are deduplicated when
building administrative hierarchy edges. ONS's current LSOA lookup has no MSOA
column, so MSOAs remain a boundary layer without a refreshed hierarchy chain.

The political lookup includes constituencies and split wards. Those are overlaps,
not a strict administrative containment chain: constituencies can cross local
council boundaries. The loader therefore imports only its ward/council/upper-tier
columns. Constituency containment is deferred until overlaps can be represented
honestly; this change does not delete any existing hierarchy edges.

ArcGIS may return an error envelope under HTTP 200. All three geography fetch
paths reject such responses instead of reporting a successful empty import.

### Code History Database

| Source | Format | URL |
|---|---|---|
| ONS Code History Database (CHD), area changes | Bulk download (zip, MS Access + CSV inside) | <https://www.ons.gov.uk/methodology/geography/geographicalproducts/namescodesandlookups/codehistorydatabasechd> |

The CHD is **not** an ArcGIS service — it's a periodic bulk drop. The loader downloads the zip, extracts `ChangeHistory.csv`, and upserts into `geography.code_change`. Refresh cadence: quarterly.

## Why BUC where available, fallback otherwise

- We only render outlines on the v1 UI; sub-100m geometric accuracy is irrelevant.
- Mac-mini storage budget for the geography spine is ~3GB. BFC for every LSOA blows that.
- For levels where BUC isn't published (LSOA 2021, MSOA 2021, Region, Country, Ward 2024), the next-smallest is small enough that the budget still holds.

## What changes if a URL goes stale

1. Update the row in this ADR.
2. Update the corresponding constant in `server/soundings/adapters/ons_geography/endpoints.py` (introduced in Task 22).
3. Re-run `make seed` (or `make seed-light` for dev). Loaders are idempotent.

## Open items

- The `(unverified)` URLs in the table above should be confirmed against the live OGP at first loader run. If a service name has changed, update both the ADR and the endpoints file.
- Scotland and Northern Ireland boundaries are not loaded at v1. The data spec (`docs/v1-orchestration-and-capture.md` §11) documents this gap.
