import { describe, expect, it } from "vitest";
import { renderSafeMarkdown } from "./safe_markdown";

describe("renderSafeMarkdown", () => {
  it("keeps useful markdown", () => {
    const html = renderSafeMarkdown("**Hello** [ONS](https://ons.gov.uk)");
    expect(html).toContain("<strong>Hello</strong>");
    expect(html).toContain('href="https://ons.gov.uk"');
  });

  it("renders raw HTML as text instead of executing it", () => {
    const html = renderSafeMarkdown("<img src=x onerror=alert(1)>");
    expect(html).not.toContain("<img");
    expect(html).toContain("&lt;img");
  });

  it("rejects javascript links and images", () => {
    const html = renderSafeMarkdown("[click](javascript:alert(1)) ![x](data:text/html,hi)");
    expect(html).not.toContain("javascript:");
    expect(html).not.toContain("data:text/html");
    expect(html).toContain("click");
  });

  it("escapes attribute-breaking URL characters", () => {
    const html = renderSafeMarkdown('[safe](https://example.com?q="oops")');
    expect(html).not.toContain('href="https://example.com?q="oops"');
  });
});
