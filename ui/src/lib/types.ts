// TypeScript mirror of `server/soundings/contracts/*` and the tool /
// capture endpoint response shapes. Kept in sync by hand per ADR-0005.
// If you change a Pydantic model server-side, update this file in the
// same commit.

export type ConsentLevel = "full" | "minimal" | "none";

export type AskerSector =
  | "charity"
  | "funder"
  | "researcher"
  | "commissioner"
  | "public"
  | "other";

export type CacheStatus = "live" | "cached" | "stale";

export type Confidence = "official" | "modelled" | "experimental";

export interface SourceRef {
  source_id: string;
  source_label: string;
  publisher: string;
  publisher_url: string;
  dataset_url: string;
  retrieved_at: string;
  cache_status: CacheStatus;
  licence: string;
}

export interface IndicatorValue {
  place_id: string;
  indicator: string;
  value: number | null;
  unit: string;
  period: string;
  source: SourceRef;
  methodology_note?: string | null;
  caveats: string[];
  confidence: Confidence;
  // Directionality from catalogue.indicator.higher_is — drives the UI's
  // good/bad framing on the benchmark badge.
  higher_is?: "better" | "worse" | "neutral" | null;
  // Percentile of this value against peer places of the same type,
  // excluding self. Populated when the indicator's peer universe is
  // loaded in data.indicator_value.
  benchmark_percentile?: number | null;
}

export interface PlaceMatch {
  id: string;
  name: string;
  type: string;
  parent_ids: string[];
  confidence: number;
}

export interface FindPlaceResponse {
  matches: PlaceMatch[];
  sources?: SourceRef[];
}

export interface GetIndicatorsResponse {
  results: IndicatorValue[];
}

export interface ObservationSummaryItem {
  theme: string;
  count: number;
  latest_submission: string;
  organisation_names: string[];
}

export interface ObservationSummary {
  total_observations: number;
  themes: ObservationSummaryItem[];
}

export interface PlaceProfile {
  place: {
    id: string;
    name: string;
    type: string;
  };
  indicators: IndicatorValue[];
  observations_summary: ObservationSummary | null;
}

// compare_places (spec §4.4 / Phase 3 Block G) ------------------------------

export type ComparisonBasis = "percentile" | "rank" | "absolute" | "rate";

export interface ComparisonValue {
  place_id: string;
  value: number | null;
  rank?: number | null;
  percentile?: number | null;
}

export interface Comparison {
  indicator: string;
  unit: string;
  period: string;
  values: ComparisonValue[];
  source: SourceRef;
  methodology_note?: string | null;
  caveats: string[];
}

export interface ComparePlacesResponse {
  results: Comparison[];
  sources?: SourceRef[];
  caveats?: string[];
  partial?: boolean;
}

// get_trend (spec §4.5 / Phase 3 Block H) -----------------------------------

export interface TrendPoint {
  period: string;
  value: number | null;
  revised?: boolean;
}

export interface Trend {
  place_id: string;
  indicator: string;
  unit: string;
  points: TrendPoint[];
  source: SourceRef;
  breaks_in_series?: string[];
}

export interface GetTrendResponse {
  trend: Trend | null;
  sources?: SourceRef[];
  caveats?: string[];
  partial?: boolean;
}

export interface ConsentResponse {
  session_id: string;
  consent_level: ConsentLevel;
  consent_version: string;
  asker_sector: AskerSector | null;
  schema_version: "v1";
}

export interface FeedbackResponse {
  ok: true;
}

// observations (Phase 7 / observations MVP) -----------------------------------

export type EvidenceType = "quantitative" | "qualitative";
export type ConfidenceLevel = "high" | "medium" | "low";

export interface ObservationRecord {
  id: string;
  organisation_id: string;
  organisation_name: string;
  place_id: string;
  place_name: string;
  period_start: string;
  period_end: string | null;
  theme: string;
  statement: string;
  indicator_key: string | null;
  value: number | null;
  unit: string | null;
  evidence_type: EvidenceType;
  methodology_note: string | null;
  confidence: ConfidenceLevel;
  submitted_at: string;
}

export interface ObservationSummaryItem {
  theme: string;
  count: number;
  latest_submission: string;
  organisation_names: string[];
}

export interface ObservationSummary {
  total_observations: number;
  themes: ObservationSummaryItem[];
}

export interface ObservationSubmit {
  organisation_id: string;
  place_id: string;
  period_start: string;
  period_end?: string | null;
  theme: string;
  statement: string;
  indicator_key?: string | null;
  value?: number | null;
  unit?: string | null;
  evidence_type: EvidenceType;
  methodology_note?: string | null;
  confidence: ConfidenceLevel;
}

export interface SubmitObservationOutput {
  status: "accepted";
  observation_id: string;
}

export interface GetObservationsResponse {
  observations: ObservationRecord[];
  total: number;
  summary: ObservationSummary | null;
  caveats: string[];
}

// contribute auth (observations MVP) ----------------------------------------

export interface RequestMagicLinkInput {
  organisation_id: string;
  email: string;
}

export interface RequestMagicLinkOutput {
  status: "link_sent";
}

export interface VerifyMagicLinkInput {
  token: string;
}

export interface VerifyMagicLinkOutput {
  status: "verified";
  organisation_id: string;
}

export interface SignupOrgInput {
  name: string;
  email: string;
  primary_place_id: string;
}

export interface SignupOrgOutput {
  status: "created" | "exists";
  organisation_id: string;
}

// find_organisations_in_place (spec §4.6 / Phase 4 Block D) ------------------------

export interface GrantRef {
  funder: string;
  amount: number;
  currency: string;
  date: string;
  purpose: string | null;
  source: SourceRef;
}

export interface OrganisationRef {
  id: string;
  name: string;
  classification: string[];
  registered_address_place_id: string | null;
  operates_in_place_ids: string[];
  operates_in_place_names: string[];
  recent_grants: GrantRef[];
  latest_income: number | null;
  register_url: string | null;
  date_of_registration: string | null;
  source: SourceRef;
  methodology_note: string | null;
}

export interface FindOrganisationsInPlaceResponse {
  organisations: OrganisationRef[];
  sources: SourceRef[];
  caveats: string[];
  partial: boolean;
}

// get_civil_society_profile (spec §5.1 / Phase 5 Block E) ----------------------

export interface IncomeBucket {
  label: string;
  lower: number;
  upper: number | null;
  count: number;
}

export interface RegistrationCohort {
  year: number;
  registered: number;
  removed: number;
  net: number;
}

export interface NotableOrg {
  id: string;
  name: string;
  register_url: string | null;
  latest_income: number | null;
  date_of_registration: string | null;
  year_registered: number | null;
}

export interface NotableOrgs {
  oldest: NotableOrg | null;
  newest: NotableOrg | null;
  largest: NotableOrg | null;
  income_concentration_top3_pct: number | null;
  income_concentration_top3_total: number | null;
}

export interface CauseAreaCount {
  label: string;
  count: number;
}

export interface FunderSummary {
  name: string;
  grant_count: number;
  total_gbp: number;
}

export interface GrantYearSummary {
  year: number;
  grant_count: number;
  total_gbp: number;
}

export interface CivilSocietyProfile {
  place_id: string;
  total_organisations: number;
  registered_address_count: number;
  with_reported_income: number;
  median_income: number | null;
  mean_income: number | null;
  income_buckets: IncomeBucket[];
  registration_cohort: RegistrationCohort[];
  top_funders: FunderSummary[];
  grants_by_year: GrantYearSummary[];
  notable: NotableOrgs;
  cause_area_distribution: CauseAreaCount[];
  sources: SourceRef[];
  caveats: string[];
  partial: boolean;
}
