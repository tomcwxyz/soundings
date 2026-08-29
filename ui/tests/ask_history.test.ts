import { beforeEach, describe, expect, it } from "vitest";
import {
  askHistoryHref,
  readAskHistory,
  recentQuestions,
  rememberQuestion,
} from "../src/lib/ask_history";

describe("ask history", () => {
  beforeEach(() => {
    localStorage.clear();
  });

  it("stores completed questions most-recent-first and deduplicates", () => {
    rememberQuestion("Summarise Stockton", "ltla24:E06000004");
    rememberQuestion("How does it compare?", "ltla24:E06000004");
    rememberQuestion("  summarise   stockton ", "ltla24:E06000004");

    const history = readAskHistory();
    expect(history).toHaveLength(2);
    expect(history[0]?.query).toBe("summarise   stockton");
    expect(history[1]?.query).toBe("How does it compare?");
  });

  it("filters recent questions to the current place", () => {
    rememberQuestion("Summarise", "ltla24:E06000004");
    rememberQuestion("Summarise", "ltla24:E08000019");

    const stockton = recentQuestions("ltla24:E06000004");
    expect(stockton).toHaveLength(1);
    expect(stockton[0]?.placeId).toBe("ltla24:E06000004");
  });

  it("builds a link back to the cached ask URL", () => {
    const href = askHistoryHref({
      query: "What is changing here?",
      placeId: "ltla24:E06000004",
      completedAt: "2026-08-29T12:00:00Z",
    });

    expect(href).toContain("/ask?");
    expect(href).toContain("q=What+is+changing+here%3F");
    expect(href).toContain("place_id=ltla24%3AE06000004");
  });
});
