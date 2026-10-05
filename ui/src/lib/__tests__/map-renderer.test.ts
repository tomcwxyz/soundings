import { describe, it, expect } from "vitest";
import {
  colourDomain,
  amenityLayerLabel,
  amenityLegendItems,
  hasFiniteValues,
  rankFractions,
  placePopupHtml,
  buildAmenityLegend,
  amenityPopupHtml,
  choroplethSemantics,
  choroplethRankFractions,
} from "../map-renderer";

describe("amenityPopupHtml", () => {
  it("shows name, type, and address when present; escapes HTML", () => {
    const html = amenityPopupHtml({
      name: "Trussell <Trust> Food Bank",
      type: "Food banks",
      address: "TS18 1AB",
    });
    expect(html).toContain("Trussell &lt;Trust&gt; Food Bank");
    expect(html).toContain("Food banks");
    expect(html).toContain("TS18 1AB");
  });
  it("omits the address line when not given", () => {
    const html = amenityPopupHtml({ name: "X", type: "Schools" });
    expect(html).toContain("Schools");
    expect(html.match(/<br\/>/g)?.length).toBe(1);
  });
});

describe("buildAmenityLegend", () => {
  it("renders one toggle button per layer with its layer id and swatch", () => {
    const el = buildAmenityLegend(
      [
        { label: "Food banks", colour: "#111" },
        { label: "Schools", colour: "#222" },
      ],
      ["amenity-infrastructure.food_banks_count", "amenity-infrastructure.schools_count"],
    );
    const buttons = el.querySelectorAll("button.legend-toggle");
    expect(buttons.length).toBe(2);
    expect(buttons[0]!.getAttribute("data-layer-id")).toBe(
      "amenity-infrastructure.food_banks_count",
    );
    expect(el.innerHTML).toContain("Food banks");
    expect(el.innerHTML).toContain("background:#222");
  });
});

describe("placePopupHtml", () => {
  it("includes name, label:value and a View place link with an encoded href", () => {
    const html = placePopupHtml({
      name: "County Durham",
      label: "Green space per person",
      value: "1,216",
      placeId: "ltla24:E06000047",
    });
    expect(html).toContain("<strong>County Durham</strong>");
    expect(html).toContain("Green space per person: 1,216");
    expect(html).toContain('href="/place/ltla24%3AE06000047"');
    expect(html).toContain("View place →");
  });

  it("omits the link when no placeId is given", () => {
    const html = placePopupHtml({ name: "X", label: "Y", value: "1" });
    expect(html).not.toContain("View place");
    expect(html).not.toContain("<a ");
  });

  it("escapes HTML in the place name", () => {
    const html = placePopupHtml({ name: "<script>", label: "L", value: "1" });
    expect(html).not.toContain("<script>");
    expect(html).toContain("&lt;script&gt;");
  });
});
import { PALETTE } from "../chart";

describe("colourDomain", () => {
  it("returns [min, mid, max] from finite values", () => {
    expect(colourDomain([10, 20, 30])).toEqual([10, 20, 30]);
  });

  it("ignores null/undefined/NaN", () => {
    expect(colourDomain([5, null, 15, undefined, NaN])).toEqual([5, 10, 15]);
  });

  it("falls back to [0, 0.5, 1] when no finite values", () => {
    expect(colourDomain([null, undefined])).toEqual([0, 0.5, 1]);
  });

  it("handles a single value (min === max)", () => {
    expect(colourDomain([7])).toEqual([7, 7, 7]);
  });
});

describe("hasFiniteValues", () => {
  it("is false when no value is a finite number", () => {
    expect(hasFiniteValues([null, undefined, NaN])).toBe(false);
    expect(hasFiniteValues([])).toBe(false);
  });
  it("is true when at least one value is finite", () => {
    expect(hasFiniteValues([null, 3, undefined])).toBe(true);
  });
});

describe("rankFractions", () => {
  it("maps smallest→0 and largest→1, preserving order", () => {
    expect(rankFractions([10, 30, 20])).toEqual([0, 1, 0.5]);
  });
  it("returns null for non-finite entries and spreads the rest", () => {
    expect(rankFractions([5, null, 15, 25])).toEqual([0, null, 0.5, 1]);
  });
  it("gives a single finite value a mid rank", () => {
    expect(rankFractions([null, 42])).toEqual([null, 0.5]);
  });
  it("returns all-null when there are no finite values", () => {
    expect(rankFractions([null, undefined])).toEqual([null, null]);
  });
});

describe("amenityLayerLabel", () => {
  it("humanises an infrastructure count key", () => {
    expect(amenityLayerLabel("infrastructure.food_banks_count")).toBe("Food banks");
    expect(amenityLayerLabel("infrastructure.gp_practices_count")).toBe("Gp practices");
  });
});

describe("amenityLegendItems", () => {
  it("assigns one PALETTE colour per layer", () => {
    const items = amenityLegendItems([
      "infrastructure.food_banks_count",
      "infrastructure.schools_count",
    ]);
    expect(items).toEqual([
      { label: "Food banks", colour: PALETTE[0] },
      { label: "Schools", colour: PALETTE[1] },
    ]);
  });
});


describe("choropleth semantics", () => {
  it("reverses PPFI deciles so the highest-priority neighbourhoods are visually strongest", () => {
    const semantics = choroplethSemantics("food.ppfi.overall_decile");
    expect(semantics.reverse).toBe(true);
    expect(semantics.lowValueLabel).toBe("Highest priority");
    expect(semantics.highValueLabel).toBe("Lowest priority");
    expect(choroplethRankFractions([1, 5, 10], "food.ppfi.overall_decile")).toEqual([
      1,
      0.5,
      0,
    ]);
  });

  it("also reverses IMD deciles, where decile 1 is most deprived", () => {
    expect(choroplethRankFractions([1, 10], "deprivation.imd.decile")).toEqual([1, 0]);
  });

  it("keeps ordinary indicators low-to-high", () => {
    const semantics = choroplethSemantics("population.total");
    expect(semantics.reverse).toBe(false);
    expect(choroplethRankFractions([10, 20], "population.total")).toEqual([0, 1]);
  });
});
