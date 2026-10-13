# Scotland and Wales: coverage audit and first Welsh source

**Status:** Feature branch; not deployed. 13 October 2026.

## Approach

Retain the question-led Phase 6.5 methodology. Nation coverage is a
property of **materialised data**, not merely the advertised catalogue. Do
not infer zero charities, grants or deprivation values from an unpopulated
source. Do not compare relative national deprivation ranks across borders.

### Code-grounded pre-import matrix

| Capability | England | Wales | Scotland |
|---|---|---|---|
| 2021 LSOA geography | ONS loader | ONS loader | **Gap**: 2022 Data Zones needed |
| Councils/country boundary layer | ONS UK layer configured | ONS UK layer configured | ONS UK layer configured, verify loaded |
| Census 2021 | ONS England/Wales | ONS England/Wales | **Gap**: Scotland Census 2022 adapter |
| Deprivation | English IoD | **This branch:** WIMD 2025 | **Gap**: SIMD edition + correct Data Zone vintage |
| Charities | Charity Commission | Charity Commission | **Gap**: FTC local lookup disabled; OSCR needed |
| 360Giving | Grant index exists; inspect populated coverage | Index may have Welsh grants; measure, do not assume | Index may have Scottish grants; measure |
| PPFI | England 2021 LSOA slice loaded | **Gap**: country slice | **Gap**: legacy Scottish geography |

These are implementation/pathway observations from the repository, **not** an
assertion that production contains every advertised source. Run the audit
script against the development database to obtain actual counts.

### Safe live-data coverage audit

`cd server && uv run python scripts/audit_national_coverage.py`

The script is read-only and outputs place counts, source-level observation
counts, distinct places and indicators for E/W/S/N country codes. Record
results in a new report, distinguishing absent geography from missing
loader data and genuinely absent observations. Separately exercise these
questions through Ask:

1. Summarise Cardiff.
2. Which neighbourhoods in Cardiff rank highest for income deprivation?
3. Which Swansea neighbourhoods have poor access to services?
4. Summarise Glasgow.
5. Which charities operate in Glasgow? (Known gap; must be partial.)
6. Compare Cardiff and Glasgow using only truly comparable measures.

For each: record supported/partial/gap; failures as geography, source,
routing, synthesis or presentation. Don't claim runtime success from the
unit test suite.

### First Welsh integration

Source: [StatsWales WIMD 2025 ranks and groups](https://stats.gov.wales/en-GB/9706edd9-73ad-4902-bb12-7ccd7038626e),
[official Welsh Government release](https://www.gov.wales/welsh-index-multiple-deprivation-2025).
API CSV export dataset ID:
`9706edd9-73ad-4902-bb12-7ccd7038626e`.

The official long-form table has Data values / Data description / Area code /
Area name / Domain, including Rank, Decile, Quintile, Quartile and Group.
Import *published* rank/decile cells only, for W010 LSOA 2021 codes.
Expected: 1,917 Welsh LSOAs, 9 domains (index + 8), up to 34,506
observations. No invented overall deprivation magnitudes or inferred trends.

Operation order (development database only):

1. Apply existing migrations, seed catalogue and ONS geography.
2. `cd server && uv run python -m soundings.loader.run --once gov_wales.wimd2025`.
3. Run `uv run python scripts/audit_national_coverage.py` again.
4. Verify Welsh place/indicator lookups and Cardiff/Swansea neighbourhood maps.
5. Review actual StatsWales export headers and licence before production rollout.

The loader fails if the official CSV is unexpectedly incomplete or contains
invalid/duplicate rank cells. It reports unmatched geography, and writes
only matched Welsh LSOA values with explicit country-comparison caveats.
No automatic cross-border proxying and no database migration in this batch.

### Next Scottish slice

Define nation-aware hierarchy types for dz22/iz22 plus dz11 legacy
boundaries, then update postcode mappings and sub-area selection. Implement
OSCR register ingestion with registered-address versus operating-area
provenance, and only after that introduce SIMD and Census 2022 indicators.

### Neighbourhood ordering safeguard

The default `get_sub_areas` sort is now `worst_first`, which reads
`catalogue.indicator.higher_is`. WIMD/PPFI rank and decile indicators
therefore select *lower* numerical values for highest deprivation or
food-access priority; English IMD scores still select higher values.
Explicit `value_asc`, `value_desc` and `name` sorts remain supported.

### Scottish 2022 geography foundation (new branch batch)

Source: Scottish Government 2022 Data Zone Lookup
(https://data.gov.scot/dataset/2022_data_zone_lookup).
Loader `gov_scot.geography2022` accepts official columns `DZ22`,
`IZ22`, `LA`. It validates 7,392 DZs and 1,334 IZs, checks all Scottish
council/country parent places exist and writes direct/transitive hierarchy
edges. A full ONS UK place seed must precede this operation. Display names
come from the lookup if provided; otherwise identifiers are used, **not**
guessed neighbourhood labels.

`cd server && uv run python -m soundings.loader.run --once gov_scot.geography2022`

This source **does not load geometries** or assign postcodes to Data Zones;
Scottish choropleths/postcode-neighbourhood resolution remain unavailable
until those separate adapters are verified. Do not mark Scottish maps as
supported just because the hierarchy is loaded.

The read-only audit now includes Scottish Data/Intermediate Zone counts
and separates organisations registered in a nation from organisations
recorded as operating there.

### Nation-aware neighbourhood selection

`get_sub_areas` now defaults to `child_type=auto`: Welsh and English
local authorities resolve to `lsoa21`, Scottish local authorities prefer
the first compatible catalogued `dz22` or `dz11` level (and default to
`dz22` if the source has no matching declared type). Explicit client
requests for `child_type` still take precedence. This is necessary for
the WIMD/SIMD distinction but does not mean Scottish deprivation data or
boundaries are loaded yet.

### Scottish 2022 Data Zone polygon service (new batch)

Verified Scottish Government 2022 Data Zone polygon layer:
https://services-eu1.arcgis.com/4QkhM5AS8YOlkb6T/arcgis/rest/services/2022_Data_Zones_and_Intermediate_Zones/FeatureServer/0.
It publishes `DZCode`, `DZName` and 7,392 polygons. The separate
`gov_scot.dz22_boundaries` loader requests paged GeoJSON reprojected to
WGS84, validates codes/shapes and upserts existing `geography.place.geom`.
It **requires** `gov_scot.geography2022` to complete beforehand. It does
not claim maps work until the complete source has been imported.

After Scottish geography import, run in development:

```bash
uv run python -m soundings.loader.run --once gov_scot.dz22_boundaries
```

Once polygons are present, Scottish postcodes with coordinates are resolved
via PostGIS `ST_Covers` into a 2022 Data Zone and its parent Intermediate
Zone. No new postcode DB column is required. This is explicitly fallback
enrichment at read time; the `geography.postcode` cache retains its
existing England/Wales-focused columns. The children map API also now uses
the same indicator/nation-aware small-area selection as `get_sub_areas`.
Large `dz22` and `dz11` national exports need `large=true`.

**Remaining limitations:** Scottish official datasets and actual geometry
download require development smoke tests. Polygon import does not by itself
provide Scottish Census, SIMD, OSCR or postcode-by-data-zone bulk reporting.

### Small-area geometry presentation

For LSOA/Data Zone child maps, a five-thousandth-of-a-degree
simplification tolerance was too coarse and could destroy small
neighbourhood outlines. Their endpoint now uses a topology-preserving
0.0002-degree tolerance (roughly tens of metres), while other areas
retain their previous simplification policy. This remains subject to
real map rendering checks.

### Scottish charities: OSCR official current register

New loader `oscr` imports the official daily-updated OSCR bulk ZIP
(https://www.oscr.org.uk/download/charity-register), without a proprietary
API key. Scheduled weekly; minimum 20,000 current entries to prevent an
incomplete/changed upstream response silently replacing existing coverage.

OSCR's Open Government Licence v3 comes with specific constraints:
no direct marketing, do not claim to publish the Scottish Charity Register,
and attribute: © Crown Copyright and database right [year]. Contains
information from the Scottish Charity Register supplied by the Office of
the Scottish Charity Regulator and licensed under the Open Government
Licence v.3.0.

Importer stores only public organisation-level metadata, NOT street addresses,
contacts or trustee names. Identifiers use `oscr:SCxxxxxx`, never overwrite
`charity_commission:` IDs. Registered councils are resolved only from
already-cached postcodes (no postcode API fanout). The published "Main
Operating Location" is captured separately, not misrepresented as the
area the charity serves. Existing OSCR rows absent from a subsequent
successful snapshot are marked not current, not hard-deleted.

`civil_society.oscr_registered_charities_count` is a separate
postcode-matched registered-address indicator. Do not combine it with CCEW
counts: dual-registered charities are possible, and missing postcodes
understate local counts.

**Still requires:** an actual upstream ZIP/CSV smoke, isolated-dev DB import,
verify application query routing to local OSCR data when present (with FTC fallback),
OSCR attribution in UI/results, dual-register reconciliation and verification
that not-current organisations don't surface. Keep draft until these pass.

Run on development after catalogue + ONS postcode seed:
`cd server && uv run python -m soundings.loader.run --once oscr`.

The Scottish organisation search is now wired into the orchestrator: when a
current OSCR snapshot is present, the query returns matched Scottish
registered-address charities rather than attempting FTC local-authority
discovery; before a successful source import, it retains the FTC fallback.
The query explicitly returns `partial=true` with licensed source
attribution and missing-address caveats. This **does not** yet provide
verified service-delivery geography, and the separate civil society profile
will need Scottish-specific implementation before full UK coverage.
