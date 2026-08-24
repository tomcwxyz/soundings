// Thin HTTP client wrapping the Soundings /v1 surface.
//
// Reads `SOUNDINGS_API_BASE` from `process.env` at request time (rather
// than `import.meta.env`, which Vite resolves at build time and would
// freeze the value into the bundle). With the @astrojs/node adapter,
// every SSR fetch runs in Node, so `process.env` reflects the compose
// `environment:` block — http://server:8000 in Docker, falling back to
// http://localhost:8000 for `npm run dev`.
// `credentials: "include"` makes the session + consent cookies
// round-trip on UI ↔ API calls.

import type {
  CivilSocietyProfile,
  ComparePlacesResponse,
  ComparisonBasis,
  ConsentLevel,
  ConsentResponse,
  FeedbackResponse,
  FindPlaceResponse,
  FindOrganisationsInPlaceResponse,
  GetIndicatorsResponse,
  GetObservationsResponse,
  GetTrendResponse,
  PlaceProfile,
} from "./types";

const DEFAULT_BASE = "http://localhost:8000";

export function apiBase(): string {
  const fromEnv =
    typeof process !== "undefined" ? process.env.SOUNDINGS_API_BASE : undefined;
  return fromEnv && fromEnv.length > 0 ? fromEnv : DEFAULT_BASE;
}

interface RequestOptions {
  cookieHeader?: string;
}

async function postJSON<T>(
  path: string,
  body: unknown,
  opts: RequestOptions = {},
): Promise<T> {
  const headers: Record<string, string> = {
    "Content-Type": "application/json",
    Accept: "application/json",
  };
  // In SSR the browser cookie jar isn't available — the page must
  // forward Astro.cookies via this header. In the browser, leaving it
  // unset means the fetch uses the document's cookies thanks to
  // `credentials: include`.
  if (opts.cookieHeader) {
    headers["Cookie"] = opts.cookieHeader;
  }
  const response = await fetch(`${apiBase()}${path}`, {
    method: "POST",
    headers,
    body: JSON.stringify(body),
    credentials: "include",
  });
  if (!response.ok) {
    throw new Error(`${path} ${response.status} ${response.statusText}`);
  }
  return (await response.json()) as T;
}

export async function findPlace(
  query: string,
  opts: { nlQuestion?: string; cookieHeader?: string } = {},
): Promise<FindPlaceResponse> {
  const body: Record<string, unknown> = { query };
  if (opts.nlQuestion) {
    body.nl_question = opts.nlQuestion;
  }
  return postJSON<FindPlaceResponse>("/v1/tools/find_place", body, {
    cookieHeader: opts.cookieHeader,
  });
}

export async function getPlaceProfile(
  placeId: string,
  include: string[] = ["population", "deprivation"],
  opts: { cookieHeader?: string } = {},
): Promise<PlaceProfile> {
  return postJSON<PlaceProfile>(
    "/v1/tools/get_place_profile",
    { place_id: placeId, include },
    opts,
  );
}

export async function getIndicators(
  placeId: string,
  indicators: string[],
  opts: { cookieHeader?: string } = {},
): Promise<GetIndicatorsResponse> {
  return postJSON<GetIndicatorsResponse>(
    "/v1/tools/get_indicators",
    { place_id: placeId, indicators },
    opts,
  );
}

export async function comparePlaces(
  placeIds: string[],
  indicators: string[],
  opts: {
    basis?: ComparisonBasis;
    period?: string | null;
    cookieHeader?: string;
  } = {},
): Promise<ComparePlacesResponse> {
  const body: Record<string, unknown> = {
    place_ids: placeIds,
    indicators,
  };
  if (opts.basis !== undefined) {
    body.comparison_basis = opts.basis;
  }
  if (opts.period !== undefined && opts.period !== null) {
    body.period = opts.period;
  }
  return postJSON<ComparePlacesResponse>("/v1/tools/compare_places", body, {
    cookieHeader: opts.cookieHeader,
  });
}

export async function getTrend(
  placeId: string,
  indicator: string,
  opts: {
    periodFrom?: string | null;
    periodTo?: string | null;
    cookieHeader?: string;
  } = {},
): Promise<GetTrendResponse> {
  const body: Record<string, unknown> = {
    place_id: placeId,
    indicator,
  };
  if (opts.periodFrom) {
    body.period_from = opts.periodFrom;
  }
  if (opts.periodTo) {
    body.period_to = opts.periodTo;
  }
  return postJSON<GetTrendResponse>("/v1/tools/get_trend", body, {
    cookieHeader: opts.cookieHeader,
  });
}

export async function postConsent(
  consentLevel: ConsentLevel,
  opts: { askerSector?: string | null; cookieHeader?: string } = {},
): Promise<ConsentResponse> {
  const body: Record<string, unknown> = { consent_level: consentLevel };
  if (opts.askerSector !== undefined) {
    body.asker_sector = opts.askerSector;
  }
  return postJSON<ConsentResponse>("/v1/capture/consent", body, {
    cookieHeader: opts.cookieHeader,
  });
}

export async function postFeedback(
  questionRecordId: string,
  markedUseful: boolean,
  opts: { cookieHeader?: string } = {},
): Promise<FeedbackResponse> {
  return postJSON<FeedbackResponse>(
    "/v1/capture/feedback",
    { question_record_id: questionRecordId, marked_useful: markedUseful },
    opts,
  );
}

export async function findOrganisationsInPlace(
  placeId: string,
  opts: {
    activityFilter?: string[];
    fundedOnly?: boolean;
    limit?: number;
    cookieHeader?: string;
  } = {},
): Promise<FindOrganisationsInPlaceResponse> {
  const body: Record<string, unknown> = {
    place_id: placeId,
  };
  if (opts.activityFilter !== undefined) {
    body.activity_filter = opts.activityFilter;
  }
  if (opts.fundedOnly !== undefined) {
    body.funded_only = opts.fundedOnly;
  }
  if (opts.limit !== undefined) {
    body.limit = opts.limit;
  }
  return postJSON<FindOrganisationsInPlaceResponse>(
    "/v1/tools/find_organisations_in_place",
    body,
    { cookieHeader: opts.cookieHeader },
  );
}

export async function getCivilSocietyProfile(
  placeId: string,
  opts: { cookieHeader?: string } = {},
): Promise<CivilSocietyProfile> {
  return postJSON<CivilSocietyProfile>(
    "/v1/tools/get_civil_society_profile",
    { place_id: placeId },
    { cookieHeader: opts.cookieHeader },
  );
}

export interface CorpusQuestion {
  id: string;
  timestamp: string;
  capture_level: string;
  question: string | null;
  tool_called: string;
  geography: { type: string; name: string }[];
  indicators: string[];
  sources: string[];
  result_status: string;
  asker_sector: string | null;
  marked_useful: boolean | null;
}

export interface CorpusManifest {
  available: boolean;
  period?: string;
  catalogue_version?: string;
  sanitisation_rules_version?: string;
  generator_git_sha?: string;
  files: { name: string; sha256: string; size_bytes: number; exists: boolean }[];
}

export async function getCorpusQuestions(
  opts: { limit?: number; cookieHeader?: string } = {},
): Promise<{ count: number; questions: CorpusQuestion[] }> {
  const params = new URLSearchParams();
  if (opts.limit) params.set("limit", String(opts.limit));
  const qs = params.toString();
  const url = `${apiBase()}/v1/corpus/recent${qs ? `?${qs}` : ""}`;
  const headers: Record<string, string> = {};
  if (opts.cookieHeader) headers["Cookie"] = opts.cookieHeader;
  const response = await fetch(url, {
    headers,
    credentials: "include",
  });
  if (!response.ok) {
    throw new Error(`/v1/corpus/recent ${response.status} ${response.statusText}`);
  }
  return (await response.json()) as { count: number; questions: CorpusQuestion[] };
}

export async function getCorpusManifest(
  opts: { cookieHeader?: string } = {},
): Promise<CorpusManifest> {
  const headers: Record<string, string> = {};
  if (opts.cookieHeader) headers["Cookie"] = opts.cookieHeader;
  const response = await fetch(`${apiBase()}/v1/corpus/manifest`, {
    headers,
    credentials: "include",
  });
  if (!response.ok) {
    throw new Error(`/v1/corpus/manifest ${response.status} ${response.statusText}`);
  }
  return (await response.json()) as CorpusManifest;
}

// get_observations (Phase 7 / observations MVP) ------------------------------

export async function getObservations(
  params: {
    place_id?: string;
    theme?: string;
    indicator_key?: string;
    organisation_id?: string;
    limit?: number;
  } = {},
  opts: { cookieHeader?: string } = {},
): Promise<GetObservationsResponse> {
  const body: Record<string, unknown> = {};
  if (params.place_id !== undefined) body.place_id = params.place_id;
  if (params.theme !== undefined) body.theme = params.theme;
  if (params.indicator_key !== undefined) body.indicator_key = params.indicator_key;
  if (params.organisation_id !== undefined)
    body.organisation_id = params.organisation_id;
  if (params.limit !== undefined) body.limit = params.limit;
  return postJSON<GetObservationsResponse>("/v1/tools/get_observations", body, {
    cookieHeader: opts.cookieHeader,
  });
}
