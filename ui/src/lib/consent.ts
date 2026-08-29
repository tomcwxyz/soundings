// Helpers for reading the consent state from the cookie jar (SSR or
// document.cookie at runtime) and choosing what the banner highlights.

import type { AskerSector, ConsentLevel } from "./types";

export const CONSENT_LEVELS: readonly ConsentLevel[] = [
  "full",
  "minimal",
  "none",
] as const;

export const ASKER_SECTORS: readonly AskerSector[] = [
  "charity",
  "funder",
  "researcher",
  "commissioner",
  "public",
  "other",
] as const;

// Match the server: until the visitor explicitly chooses, nothing is captured.
export const DEFAULT_CONSENT_LEVEL: ConsentLevel = "none";

export interface ConsentState {
  consentLevel: ConsentLevel;
  askerSector: AskerSector | null;
  hasConsentChoice: boolean;
}

export function readConsentFromCookieString(
  cookieString: string | null | undefined,
): ConsentState {
  const cookies = parseCookieString(cookieString ?? "");
  const rawConsent = cookies.get("soundings_consent");
  return {
    consentLevel: parseLevel(rawConsent),
    askerSector: parseSector(cookies.get("soundings_sector")),
    hasConsentChoice:
      rawConsent !== undefined &&
      (CONSENT_LEVELS as readonly string[]).includes(rawConsent),
  };
}

function parseCookieString(input: string): Map<string, string> {
  const out = new Map<string, string>();
  for (const pair of input.split(";")) {
    const trimmed = pair.trim();
    if (!trimmed) continue;
    const eq = trimmed.indexOf("=");
    if (eq < 0) continue;
    const name = trimmed.slice(0, eq);
    const value = decodeURIComponent(trimmed.slice(eq + 1));
    out.set(name, value);
  }
  return out;
}

function parseLevel(value: string | undefined): ConsentLevel {
  if (value && (CONSENT_LEVELS as readonly string[]).includes(value)) {
    return value as ConsentLevel;
  }
  return DEFAULT_CONSENT_LEVEL;
}

function parseSector(value: string | undefined): AskerSector | null {
  if (value && (ASKER_SECTORS as readonly string[]).includes(value)) {
    return value as AskerSector;
  }
  return null;
}
