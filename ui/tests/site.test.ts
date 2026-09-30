import { afterEach, describe, expect, it } from "vitest";
import { siteOrigin } from "../src/lib/site";

const ENV_KEY = "SOUNDINGS_PUBLIC_SITE_URL";

afterEach(() => {
  delete process.env[ENV_KEY];
});

describe("siteOrigin()", () => {
  it("falls back to the request origin when the env var is unset", () => {
    expect(siteOrigin("http://localhost:4321")).toBe("http://localhost:4321");
  });

  it("prefers the configured public site URL over the request origin", () => {
    process.env[ENV_KEY] = "https://data.good-ship.co.uk";
    expect(siteOrigin("http://localhost")).toBe("https://data.good-ship.co.uk");
  });

  it("normalises a configured value with a trailing slash or path to its origin", () => {
    process.env[ENV_KEY] = "https://data.good-ship.co.uk/";
    expect(siteOrigin("http://localhost")).toBe("https://data.good-ship.co.uk");

    process.env[ENV_KEY] = "https://data.good-ship.co.uk/some/path";
    expect(siteOrigin("http://localhost")).toBe("https://data.good-ship.co.uk");
  });

  it("falls back to the request origin when the configured value is empty", () => {
    process.env[ENV_KEY] = "";
    expect(siteOrigin("http://localhost:4321")).toBe("http://localhost:4321");
  });

  it("falls back to the request origin rather than throwing on a malformed value", () => {
    process.env[ENV_KEY] = "not a url";
    expect(siteOrigin("http://localhost:4321")).toBe("http://localhost:4321");
  });
});
