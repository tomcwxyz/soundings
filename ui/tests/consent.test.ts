import { describe, expect, it } from "vitest";
import {
  ASKER_SECTORS,
  CONSENT_LEVELS,
  DEFAULT_CONSENT_LEVEL,
  readConsentFromCookieString,
} from "../src/lib/consent";

describe("readConsentFromCookieString", () => {
  it("defaults to no capture until the visitor chooses", () => {
    expect(readConsentFromCookieString(null)).toEqual({
      consentLevel: DEFAULT_CONSENT_LEVEL,
      askerSector: null,
      hasConsentChoice: false,
    });
    expect(DEFAULT_CONSENT_LEVEL).toBe("none");
  });

  it("reads a valid consent + sector pair", () => {
    const state = readConsentFromCookieString(
      "soundings_consent=full; soundings_sector=charity; soundings_session=abc",
    );
    expect(state.consentLevel).toBe("full");
    expect(state.askerSector).toBe("charity");
    expect(state.hasConsentChoice).toBe(true);
  });

  it("distinguishes an explicit no-consent choice from no choice", () => {
    const state = readConsentFromCookieString("soundings_consent=none");
    expect(state.consentLevel).toBe("none");
    expect(state.hasConsentChoice).toBe(true);
  });

  it("falls back to no capture for unknown consent values", () => {
    const state = readConsentFromCookieString("soundings_consent=partial");
    expect(state.consentLevel).toBe(DEFAULT_CONSENT_LEVEL);
    expect(state.hasConsentChoice).toBe(false);
  });

  it("clears unknown sector values to null", () => {
    const state = readConsentFromCookieString(
      "soundings_consent=full; soundings_sector=philanthropist",
    );
    expect(state.askerSector).toBeNull();
  });
});

describe("consent vocabularies", () => {
  it("matches the server-side vocabularies", () => {
    expect(CONSENT_LEVELS).toEqual(["full", "minimal", "none"]);
    expect(ASKER_SECTORS).toEqual([
      "charity",
      "funder",
      "researcher",
      "commissioner",
      "public",
      "other",
    ]);
  });
});
