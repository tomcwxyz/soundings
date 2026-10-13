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
