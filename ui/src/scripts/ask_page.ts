      import { streamAsk } from "../lib/answer_stream";
      import { marked } from "marked";
      import type { ComparePlacesResponse } from "../lib/types";

      // Non-null assertion: the runtime `if (surface)` guard below still
      // protects against a missing element; the assertion only keeps the
      // closures that capture `surface` from widening it back to null.
      const surface = document.getElementById("answer-surface")!;
      if (surface) {
        const apiBase = surface.dataset.apiBase ?? "";
        const mapTilesUrl = surface.dataset.mapTiles ?? "";
        const placeId = surface.dataset.placeId || undefined;
        // Read the question from the surface's data attribute — NOT
        // `querySelector("h1")`, which returns the layout's "Soundings" header
        // (the first h1 on the page) instead of the question.
        const query = surface.dataset.query || "";

        // --- Processing steps (animated) ------------------------------------
        const STEP_LABELS: Record<string, string> = {
          find_place: "Finding the place",
          get_indicators: "Looking up indicators",
          get_place_profile: "Building the place profile",
          compare_places: "Comparing places",
          get_trend: "Fetching trends over time",
          detect_insights: "Detecting notable signals",
          get_peer_distribution: "Comparing against peers",
          get_sub_areas: "Loading neighbourhoods",
          find_organisations_in_place: "Finding organisations",
          get_civil_society_profile: "Profiling civil society",
          compose_answer: "Composing the answer",
        };

        function friendlyStep(message: string): string {
          const tool = message.match(/Calling (\w+)/)?.[1];
          return (tool && STEP_LABELS[tool]) || message.replace(/…$/, "");
        }

        let stepsEl: HTMLOListElement | null = null;
        function pushStep(label: string) {
          if (!stepsEl) {
            stepsEl = document.createElement("ol");
            stepsEl.className = "answer-steps";
            surface.prepend(stepsEl);
          }
          const active = stepsEl.querySelector<HTMLElement>(".step.is-active");
          if (active) {
            active.classList.remove("is-active");
            active.classList.add("is-done");
          }
          const li = document.createElement("li");
          li.className = "step is-active";
          const icon = document.createElement("span");
          icon.className = "step-icon";
          const text = document.createElement("span");
          text.className = "step-label";
          text.textContent = label;
          li.append(icon, text);
          stepsEl.appendChild(li);
        }
        function finishSteps() {
          const active = stepsEl?.querySelector<HTMLElement>(".step.is-active");
          if (active) {
            active.classList.remove("is-active");
            active.classList.add("is-done");
          }
        }

        function renderMarkdown(text: string): string {
          // Full GitHub-flavoured markdown: headings, tables, blockquotes,
          // ordered/unordered lists, bold/italic/code. `gfm` is on by default.
          // `breaks` keeps single newlines as <br>, matching how the model
          // tends to write. marked.parse is synchronous with no async tokens.
          return marked.parse(text, { async: false, gfm: true, breaks: true }) as string;
        }

        // --- Block renderers -------------------------------------------------

        // Shared POST helper for the /v1/tools/* endpoints. The browser
        // cookie jar is used automatically via `credentials: "include"`.
        async function postJSON<T>(
          path: string,
          body: unknown,
          base: string,
        ): Promise<T> {
          const response = await fetch(base + path, {
            method: "POST",
            headers: {
              "Content-Type": "application/json",
              Accept: "application/json",
            },
            body: JSON.stringify(body),
            credentials: "include",
          });
          if (!response.ok) {
            throw new Error(`${path} ${response.status} ${response.statusText}`);
          }
          return (await response.json()) as T;
        }

        async function getJSON<T>(path: string, base: string): Promise<T> {
          const response = await fetch(base + path, {
            headers: { Accept: "application/json" },
            credentials: "include",
          });
          if (!response.ok) {
            throw new Error(`${path} ${response.status} ${response.statusText}`);
          }
          return (await response.json()) as T;
        }

        function showBlockError(host: HTMLElement, message: string) {
          const el = document.createElement("p");
          el.className = "block-error";
          el.textContent = message;
          host.appendChild(el);
        }

        function asString(v: unknown): string {
          return typeof v === "string" ? v : "";
        }

        function asStringOrUndef(v: unknown): string | undefined {
          return typeof v === "string" && v.length > 0 ? v : undefined;
        }

        function asStringArray(v: unknown): string[] {
          return Array.isArray(v)
            ? v.filter((x): x is string => typeof x === "string")
            : [];
        }

        function formatValue(value: number | null): string {
          if (value === null) return "—";
          if (!Number.isFinite(value)) return "—";
          if (Number.isInteger(value)) return value.toLocaleString("en-GB");
          const abs = Math.abs(value);
          if (abs === 0) return "0";
          if (abs >= 1000) return value.toLocaleString("en-GB", { maximumFractionDigits: 1 });
          if (abs >= 1) return value.toLocaleString("en-GB", { maximumFractionDigits: 2 });
          if (abs >= 0.01) return value.toLocaleString("en-GB", { maximumFractionDigits: 3 });
          if (abs >= 0.0001) return value.toLocaleString("en-GB", { maximumFractionDigits: 5 });
          return value.toLocaleString("en-GB", { maximumFractionDigits: 8 });
        }

        function prettyKey(key: string): string {
          const [head, ...rest] = key.split(".");
          const headPretty = head
            ? head[0]!.toUpperCase() + head.slice(1)
            : key;
          const tail = rest.join(" · ").replaceAll("_", " ");
          return tail ? `${headPretty}: ${tail}` : headPretty;
        }

        // indicator-card -----------------------------------------------------

        interface IndicatorValueLike {
          place_id: string;
          indicator: string;
          value: number | null;
          unit: string;
          period: string;
          source: { source_label: string; cache_status?: string };
          confidence?: string;
          higher_is?: string | null;
          benchmark_percentile?: number | null;
          caveats?: string[];
        }

        interface PlaceProfileResponse {
          place: { id: string; name: string; type: string };
          indicators: IndicatorValueLike[];
        }

        async function renderIndicatorCard(
          host: HTMLElement,
          block: { type: string; [k: string]: unknown },
          apiBase: string,
        ) {
          const indicatorKey = asString(block.indicator_key);
          const placeId = asString(block.place_id);
          const period = asStringOrUndef(block.period);
          if (!indicatorKey || !placeId) {
            showBlockError(host, "Indicator card missing indicator_key or place_id.");
            return;
          }
          let profile: PlaceProfileResponse;
          try {
            profile = await postJSON<PlaceProfileResponse>(
              "/v1/tools/get_place_profile",
              { place_id: placeId, include: [] },
              apiBase,
            );
          } catch (err) {
            showBlockError(
              host,
              "Could not load indicator: " +
                (err instanceof Error ? err.message : String(err)),
            );
            return;
          }
          const matches = profile.indicators.filter(
            (iv) => iv.indicator === indicatorKey,
          );
          const ind =
            matches.length > 1 && period
              ? matches.find((iv) => iv.period === period) ?? matches[0]
              : matches[0];
          if (!ind) {
            showBlockError(
              host,
              `No data for indicator "${indicatorKey}" at ${placeId}.`,
            );
            return;
          }
          host.appendChild(buildIndicatorCard(ind));
        }

        function buildIndicatorCard(ind: IndicatorValueLike): HTMLElement {
          const article = document.createElement("article");
          article.className = "indicator-card";
          const header = document.createElement("header");
          const h4 = document.createElement("h4");
          h4.textContent = prettyKey(ind.indicator);
          header.appendChild(h4);
          if (ind.source.cache_status) {
            const cache = document.createElement("span");
            cache.className = "cache-status cache-" + ind.source.cache_status;
            cache.textContent = ind.source.cache_status;
            header.appendChild(cache);
          }
          article.appendChild(header);

          const valueRow = document.createElement("div");
          valueRow.className = "value-row";
          const valueP = document.createElement("p");
          valueP.className = "value";
          const numberSpan = document.createElement("span");
          numberSpan.className = "number";
          numberSpan.textContent = formatValue(ind.value);
          valueP.appendChild(numberSpan);
          if (ind.unit) {
            const unitSpan = document.createElement("span");
            unitSpan.className = "unit";
            unitSpan.textContent = ind.unit;
            valueP.appendChild(unitSpan);
          }
          valueRow.appendChild(valueP);

          const benchmark =
            typeof ind.benchmark_percentile === "number"
              ? ind.benchmark_percentile
              : null;
          if (benchmark !== null) {
            const bench = document.createElement("div");
            bench.className = "benchmark";
            const ctx = document.createElement("span");
            ctx.className = "context";
            ctx.textContent = "p" + Math.round(benchmark);
            bench.appendChild(ctx);
            const higherIs = ind.higher_is ?? null;
            if (higherIs === "better" || higherIs === "worse") {
              const dir = document.createElement("span");
              dir.className =
                "direction " +
                (higherIs === "better" ? "good" : "bad");
              dir.textContent = higherIs === "better" ? "↑" : "↓";
              bench.appendChild(dir);
            }
            valueRow.appendChild(bench);
          }
          article.appendChild(valueRow);

          const metaRow = document.createElement("div");
          metaRow.className = "meta-row";
          const periodP = document.createElement("p");
          periodP.className = "period";
          periodP.textContent = ind.period + " · ";
          const sourceLabel = document.createElement("span");
          sourceLabel.className = "source-label";
          sourceLabel.textContent = ind.source.source_label;
          periodP.appendChild(sourceLabel);
          metaRow.appendChild(periodP);
          if (ind.confidence) {
            const badge = document.createElement("span");
            badge.className = "confidence-badge " + ind.confidence;
            badge.textContent = ind.confidence;
            metaRow.appendChild(badge);
          }
          article.appendChild(metaRow);

          if (ind.caveats && ind.caveats.length > 0) {
            const ul = document.createElement("ul");
            ul.className = "caveats";
            for (const c of ind.caveats) {
              const li = document.createElement("li");
              li.textContent = c;
              ul.appendChild(li);
            }
            article.appendChild(ul);
          }
          return article;
        }

        // trend-chart --------------------------------------------------------

        interface TrendResponse {
          trend: {
            place_id: string;
            indicator: string;
            unit: string;
            points: { period: string; value: number | null; revised?: boolean }[];
          } | null;
        }

        async function renderTrendChartBlock(
          host: HTMLElement,
          block: { type: string; [k: string]: unknown },
          apiBase: string,
        ) {
          const indicatorKey = asString(block.indicator_key);
          const caption = asStringOrUndef(block.caption);
          if (!indicatorKey) {
            showBlockError(host, "Trend chart missing indicator_key.");
            return;
          }
          // The block carries place_id; fall back to the page-level place
          // context if the server omitted it.
          const trendPlaceId = asStringOrUndef(block.place_id) ?? placeId;
          if (!trendPlaceId) {
            showBlockError(host, "Trend chart missing place_id.");
            return;
          }
          let trend: TrendResponse;
          try {
            trend = await postJSON<TrendResponse>(
              "/v1/tools/get_trend",
              { place_id: trendPlaceId, indicator: indicatorKey },
              apiBase,
            );
          } catch (err) {
            showBlockError(
              host,
              "Could not load trend: " +
                (err instanceof Error ? err.message : String(err)),
            );
            return;
          }
          if (!trend.trend || trend.trend.points.length === 0) {
            showBlockError(host, "No trend data available.");
            return;
          }
          const { renderTrendChart } = await import("../lib/chart");
          const svg = renderTrendChart(
            {
              points: trend.trend.points,
              unit: trend.trend.unit,
              caption,
            },
            { containerWidth: host.clientWidth || 480 },
          );
          if (!svg) {
            showBlockError(host, "No trend data available.");
            return;
          }
          const figure = document.createElement("figure");
          figure.className = "trend-chart-block";
          const chartDiv = document.createElement("div");
          chartDiv.className = "chart";
          chartDiv.innerHTML = svg;
          figure.appendChild(chartDiv);
          if (caption) {
            const figcaption = document.createElement("figcaption");
            figcaption.textContent = caption;
            figure.appendChild(figcaption);
          }
          host.appendChild(figure);
        }

        // compare-chart ------------------------------------------------------

        async function renderCompareChartBlock(
          host: HTMLElement,
          block: { type: string; [k: string]: unknown },
          apiBase: string,
        ) {
          const indicatorKey = asString(block.indicator_key);
          const placeIds = asStringArray(block.place_ids);
          const basis = asStringOrUndef(block.basis) ?? "percentile";
          if (!indicatorKey || placeIds.length < 2) {
            showBlockError(
              host,
              "Compare chart needs an indicator_key and at least two place_ids.",
            );
            return;
          }
          let compare: ComparePlacesResponse;
          try {
            compare = await postJSON<ComparePlacesResponse>(
              "/v1/tools/compare_places",
              {
                place_ids: placeIds,
                indicators: [indicatorKey],
                comparison_basis: basis,
              },
              apiBase,
            );
          } catch (err) {
            showBlockError(
              host,
              "Could not load comparison: " +
                (err instanceof Error ? err.message : String(err)),
            );
            return;
          }
          const comparison = compare.results.find(
            (r) => r.indicator === indicatorKey,
          );
          if (!comparison || comparison.values.length === 0) {
            showBlockError(host, "No comparison data available.");
            return;
          }
          const heading = document.createElement("h4");
          heading.className = "compare-heading";
          heading.textContent = prettyKey(comparison.indicator);
          host.appendChild(heading);
          const { renderCompareBars } = await import("../lib/chart");
          const svg = renderCompareBars(comparison, {
            basis: basis as "percentile" | "rank" | "absolute" | "rate",
            containerWidth: host.clientWidth || 480,
          });
          if (!svg) {
            showBlockError(host, "No comparison data available.");
            return;
          }
          const chartDiv = document.createElement("div");
          chartDiv.className = "chart";
          chartDiv.innerHTML = svg;
          host.appendChild(chartDiv);
        }

        // organisations -------------------------------------------------------

        interface OrganisationsResponse {
          organisations: {
            id: string;
            name: string;
            classification: string[];
            recent_grants: {
              funder: string;
              amount: number;
              currency: string;
              date: string;
              purpose: string | null;
            }[];
            latest_income: number | null;
            register_url: string | null;
            date_of_registration: string | null;
          }[];
        }

        async function renderOrganisationsBlock(
          host: HTMLElement,
          block: { type: string; [k: string]: unknown },
          apiBase: string,
        ) {
          const placeId = asString(block.place_id);
          const limit =
            typeof block.limit === "number" && block.limit > 0
              ? Math.floor(block.limit)
              : 5;
          if (!placeId) {
            showBlockError(host, "Organisations block missing place_id.");
            return;
          }
          let orgs: OrganisationsResponse;
          try {
            orgs = await postJSON<OrganisationsResponse>(
              "/v1/tools/find_organisations_in_place",
              { place_id: placeId, limit },
              apiBase,
            );
          } catch (err) {
            showBlockError(
              host,
              "Could not load organisations: " +
                (err instanceof Error ? err.message : String(err)),
            );
            return;
          }
          if (orgs.organisations.length === 0) {
            const p = document.createElement("p");
            p.className = "text-muted";
            p.textContent = "No organisations found for this place.";
            host.appendChild(p);
            return;
          }
          const list = document.createElement("div");
          list.className = "org-list";
          for (const org of orgs.organisations) {
            const card = document.createElement("article");
            card.className = "card org-card";
            const h4 = document.createElement("h4");
            if (org.register_url) {
              const link = document.createElement("a");
              link.href = org.register_url;
              link.target = "_blank";
              link.rel = "noopener noreferrer";
              link.textContent = org.name;
              h4.appendChild(link);
            } else {
              h4.textContent = org.name;
            }
            card.appendChild(h4);
            if (org.latest_income !== null && org.latest_income !== undefined) {
              const incomeP = document.createElement("p");
              incomeP.className = "org-income";
              incomeP.textContent = org.latest_income.toLocaleString("en-GB", {
                style: "currency",
                currency: "GBP",
                maximumFractionDigits: 0,
              }) + "/yr";
              card.appendChild(incomeP);
            }
            if (org.date_of_registration) {
              const year = org.date_of_registration.substring(0, 4);
              const foundedP = document.createElement("p");
              foundedP.className = "text-muted text-small";
              foundedP.textContent = `Founded ${year}`;
              card.appendChild(foundedP);
            }
            if (org.classification.length > 0) {
              const tags = document.createElement("div");
              tags.className = "org-tags";
              for (const tag of org.classification.slice(0, 5)) {
                const chip = document.createElement("span");
                chip.className = "org-tag";
                chip.textContent = tag;
                tags.appendChild(chip);
              }
              card.appendChild(tags);
            }
            // Show "also operates in" when the charity operates in
            // places beyond the one being queried.
            if (org.operates_in_place_names && org.operates_in_place_names.length > 1) {
              const otherNames = org.operates_in_place_names.filter(
                (n: string) => n !== "",
              );
              if (otherNames.length > 1) {
                const operates = document.createElement("p");
                operates.className = "text-muted text-small org-also-in";
                operates.textContent = `Also operates in ${otherNames.slice(1, 6).join(", ")}${otherNames.length > 6 ? " +" + (otherNames.length - 6) : ""}`;
                card.appendChild(operates);
              }
            }
            if (org.recent_grants.length > 0) {
              const grants = document.createElement("ul");
              grants.className = "org-grants";
              for (const g of org.recent_grants) {
                const li = document.createElement("li");
                const amount = g.amount.toLocaleString("en-GB", {
                  style: "currency",
                  currency: g.currency || "GBP",
                  maximumFractionDigits: 0,
                });
                li.textContent = `${g.funder}: ${amount} (${g.date})`;
                if (g.purpose) {
                  const purpose = document.createElement("span");
                  purpose.className = "text-muted";
                  purpose.textContent = " — " + g.purpose;
                  li.appendChild(purpose);
                }
                grants.appendChild(li);
              }
              card.appendChild(grants);
            }
            list.appendChild(card);
          }
          host.appendChild(list);
        }

        // map -----------------------------------------------------------------

        let maplibreCssLoaded = false;
        function ensureMaplibreCss() {
          if (maplibreCssLoaded) return;
          maplibreCssLoaded = true;
          const link = document.createElement("link");
          link.rel = "stylesheet";
          link.href =
            "https://unpkg.com/maplibre-gl@5/dist/maplibre-gl.css";
          document.head.appendChild(link);
        }

        async function renderMapBlock(
          host: HTMLElement,
          block: { type: string; [k: string]: unknown },
          apiBase: string,
        ) {
          const mapPlaceId = asString(block.place_id);
          const indicatorKey = asStringOrUndef(block.indicator_key);
          const granularity = asStringOrUndef(block.granularity) ?? "peers";
          const period = asStringOrUndef(block.period);
          const caption = asStringOrUndef(block.caption);
          const overlay = block.overlay as
            | { source?: string; indicator_keys?: unknown }
            | undefined;
          if (!mapPlaceId) {
            showBlockError(host, "Map block missing place_id.");
            return;
          }
          ensureMaplibreCss();
          const container = document.createElement("div");
          container.className = "map-container";
          host.appendChild(container);

          try {
            const { renderPlaceMap, renderChoroplethMap, renderAmenityMap, renderOrganisationMap } =
              await import("../lib/map-renderer");

            const amenityKeys =
              overlay?.source === "amenities" ? asStringArray(overlay.indicator_keys) : [];

            // Choropleth features: sub-area (LSOA) with a peers fallback when the
            // indicator has no sub-area data, otherwise the peer view.
            const fetchChoroplethFc = async (): Promise<GeoJSON.FeatureCollection> => {
              const q =
                `?indicator=${encodeURIComponent(indicatorKey!)}` +
                (period ? `&period=${encodeURIComponent(period)}` : "");
              if (granularity === "sub_areas") {
                const fc = await getJSON<GeoJSON.FeatureCollection>(
                  `/v1/place/${encodeURIComponent(mapPlaceId)}/children/geometry${q}`,
                  apiBase,
                );
                if (fc.features && fc.features.length > 0) return fc;
              }
              return getJSON<GeoJSON.FeatureCollection>(
                `/v1/place/${encodeURIComponent(mapPlaceId)}/peers/geometry${q}`,
                apiBase,
              );
            };

            const fetchAmenityPoints = (): Promise<GeoJSON.FeatureCollection> =>
              getJSON<GeoJSON.FeatureCollection>(
                `/v1/place/${encodeURIComponent(mapPlaceId)}/amenities/geometry` +
                  `?indicators=${encodeURIComponent(amenityKeys.join(","))}`,
                apiBase,
              );

            if (indicatorKey && overlay?.source === "amenities" && amenityKeys.length > 0) {
              // 1) combined choropleth + amenity points.
              const [fc, points] = await Promise.all([
                fetchChoroplethFc(),
                fetchAmenityPoints(),
              ]);
              renderChoroplethMap(container, fc, "value", {
                label: prettyKey(indicatorKey),
                tilesUrl: mapTilesUrl || undefined,
                points,
              });
            } else if (overlay?.source === "amenities") {
              // 2) amenity points only.
              if (amenityKeys.length === 0) {
                showBlockError(host, "Amenity overlay missing indicator_keys.");
                container.remove();
                return;
              }
              const [boundary, points] = await Promise.all([
                getJSON<GeoJSON.Feature>(
                  `/v1/place/${encodeURIComponent(mapPlaceId)}/geometry`,
                  apiBase,
                ),
                fetchAmenityPoints(),
              ]);
              renderAmenityMap(container, boundary, points, {
                tilesUrl: mapTilesUrl || undefined,
              });
            } else if (overlay?.source === "organisations") {
              // 5) organisation (charity) points only.
              const [boundary, points] = await Promise.all([
                getJSON<GeoJSON.Feature>(
                  `/v1/place/${encodeURIComponent(mapPlaceId)}/geometry`,
                  apiBase,
                ),
                getJSON<GeoJSON.FeatureCollection>(
                  `/v1/place/${encodeURIComponent(mapPlaceId)}/organisations/geometry?limit=200`,
                  apiBase,
                ),
              ]);
              renderOrganisationMap(container, boundary, points, {
                tilesUrl: mapTilesUrl || undefined,
              });
            } else if (indicatorKey) {
              // 3) choropleth (sub-area with peers fallback, or peers).
              const fc = await fetchChoroplethFc();
              renderChoroplethMap(container, fc, "value", {
                label: prettyKey(indicatorKey),
                tilesUrl: mapTilesUrl || undefined,
              });
            } else {
              // 4) boundary only.
              const feature = await getJSON<GeoJSON.Feature>(
                `/v1/place/${encodeURIComponent(mapPlaceId)}/geometry`,
                apiBase,
              );
              renderPlaceMap(container, feature, { tilesUrl: mapTilesUrl || undefined });
            }
          } catch (err) {
            container.remove();
            showBlockError(
              host,
              "Could not load map: " + (err instanceof Error ? err.message : String(err)),
            );
            return;
          }

          if (caption) {
            const figcaption = document.createElement("p");
            figcaption.className = "map-caption text-muted text-small";
            figcaption.textContent = caption;
            host.appendChild(figcaption);
          }
        }

        // distribution-chart -------------------------------------------------

        interface PeerDistributionResponse {
          indicator_key: string;
          place_id: string;
          focal_value: number | null;
          peer_values: number[];
          peer_count: number;
          unit: string;
          period: string;
        }

        async function renderDistributionChartBlock(
          host: HTMLElement,
          block: { type: string; [k: string]: unknown },
          apiBase: string,
        ) {
          const indicatorKey = asString(block.indicator_key);
          const caption = asStringOrUndef(block.caption);
          const distPlaceId = asStringOrUndef(block.place_id) ?? placeId;
          if (!indicatorKey) {
            showBlockError(host, "Distribution chart missing indicator_key.");
            return;
          }
          if (!distPlaceId) {
            showBlockError(host, "Distribution chart missing place_id.");
            return;
          }
          let dist: PeerDistributionResponse;
          try {
            dist = await postJSON<PeerDistributionResponse>(
              "/v1/tools/get_peer_distribution",
              { indicator_key: indicatorKey, place_id: distPlaceId },
              apiBase,
            );
          } catch (err) {
            showBlockError(
              host,
              "Could not load peer distribution: " +
                (err instanceof Error ? err.message : String(err)),
            );
            return;
          }
          if (!dist.peer_values || dist.peer_values.length === 0) {
            showBlockError(host, "No peer distribution data available.");
            return;
          }
          const { renderDistributionChart } = await import("../lib/chart");
          const svg = renderDistributionChart(
            {
              peer_values: dist.peer_values,
              focal_value: dist.focal_value,
              unit: dist.unit,
              peer_count: dist.peer_count,
              caption,
            },
            { containerWidth: host.clientWidth || 480 },
          );
          if (!svg) {
            showBlockError(host, "No peer distribution data available.");
            return;
          }
          const figure = document.createElement("figure");
          figure.className = "distribution-chart-block";
          const chartDiv = document.createElement("div");
          chartDiv.className = "chart";
          chartDiv.innerHTML = svg;
          figure.appendChild(chartDiv);
          if (caption) {
            const figcaption = document.createElement("figcaption");
            figcaption.textContent = caption;
            figure.appendChild(figcaption);
          }
          host.appendChild(figure);
        }

        // composition-chart --------------------------------------------------

        interface CompositionSegmentBlock {
          label: string;
          value: number;
          colour?: string | null;
        }

        function asNumber(v: unknown): number {
          return typeof v === "number" && Number.isFinite(v) ? v : 0;
        }

        function asSegments(v: unknown): CompositionSegmentBlock[] {
          if (!Array.isArray(v)) return [];
          return v
            .map((s): CompositionSegmentBlock | null => {
              if (typeof s !== "object" || s === null) return null;
              const label =
                typeof (s as Record<string, unknown>).label === "string"
                  ? (s as Record<string, unknown>).label as string
                  : "";
              const value = asNumber((s as Record<string, unknown>).value);
              const colourRaw = (s as Record<string, unknown>).colour;
              const colour =
                typeof colourRaw === "string" && colourRaw.length > 0
                  ? colourRaw
                  : undefined;
              return { label, value, colour };
            })
            .filter((s): s is CompositionSegmentBlock => s !== null);
        }

        async function renderCompositionChartBlock(
          host: HTMLElement,
          block: { type: string; [k: string]: unknown },
        ) {
          const title = asString(block.title) || prettyKey(asString(block.indicator_key));
          const caption = asStringOrUndef(block.caption);
          const segments = asSegments(block.segments);
          if (segments.length === 0) {
            showBlockError(
              host,
              "Composition chart missing segments.",
            );
            return;
          }
          const { renderCompositionChart } = await import("../lib/chart");
          const svg = renderCompositionChart(
            { title, segments, caption },
            { containerWidth: host.clientWidth || 480 },
          );
          if (!svg) {
            showBlockError(host, "No composition data available.");
            return;
          }
          const figure = document.createElement("figure");
          figure.className = "composition-chart-block";
          const chartDiv = document.createElement("div");
          chartDiv.className = "chart";
          chartDiv.innerHTML = svg;
          figure.appendChild(chartDiv);
          if (title) {
            const h4 = document.createElement("h4");
            h4.className = "composition-title";
            h4.textContent = title;
            figure.insertBefore(h4, figure.firstChild);
          }
          if (caption) {
            const figcaption = document.createElement("figcaption");
            figcaption.textContent = caption;
            figure.appendChild(figcaption);
          }
          host.appendChild(figure);
        }

        // bar-chart ----------------------------------------------------------

        interface BarChartBarBlock {
          label: string;
          value: number;
          colour?: string | null;
        }

        async function renderBarChartBlock(
          host: HTMLElement,
          block: { type: string; [k: string]: unknown },
        ) {
          const title = asString(block.title) || "Bar chart";
          const caption = asStringOrUndef(block.caption);
          const barsRaw = block.bars;
          if (!Array.isArray(barsRaw)) {
            showBlockError(host, "Bar chart missing bars.");
            return;
          }
          const bars: BarChartBarBlock[] = barsRaw
            .map((b: unknown): BarChartBarBlock | null => {
              if (typeof b !== "object" || b === null) return null;
              const r = b as Record<string, unknown>;
              const label = typeof r.label === "string" ? r.label : String(r.label ?? "");
              const value = asNumber(r.value);
              const colourRaw = r.colour;
              const colour = typeof colourRaw === "string" && colourRaw.length > 0 ? colourRaw : undefined;
              return { label, value, colour };
            })
            .filter((b): b is BarChartBarBlock => b !== null);
          if (bars.length === 0) {
            showBlockError(host, "Bar chart has no bars.");
            return;
          }
          const maxVal = Math.max(...bars.map((b) => b.value), 0.001);
          const figure = document.createElement("figure");
          figure.className = "bar-chart-block";
          const h4 = document.createElement("h4");
          h4.className = "bar-chart-title";
          h4.textContent = title;
          figure.appendChild(h4);
          const chartDiv = document.createElement("div");
          chartDiv.className = "bar-chart";
          for (const bar of bars) {
            const row = document.createElement("div");
            row.className = "bar-row";
            const labelEl = document.createElement("span");
            labelEl.className = "bar-label";
            labelEl.textContent = bar.label;
            const barWrap = document.createElement("div");
            barWrap.className = "bar-track";
            const fill = document.createElement("div");
            fill.className = "bar-fill";
            const pct = Math.max((bar.value / maxVal) * 100, 1);
            fill.style.width = pct + "%";
            if (bar.colour) fill.style.background = bar.colour;
            const valEl = document.createElement("span");
            valEl.className = "bar-value";
            valEl.textContent = bar.value.toLocaleString("en-GB", { maximumFractionDigits: 0 });
            barWrap.appendChild(fill);
            barWrap.appendChild(valEl);
            row.appendChild(labelEl);
            row.appendChild(barWrap);
            chartDiv.appendChild(row);
          }
          figure.appendChild(chartDiv);
          if (caption) {
            const figcaption = document.createElement("figcaption");
            figcaption.textContent = caption;
            figure.appendChild(figcaption);
          }
          host.appendChild(figure);
        }

        // scatter-plot -------------------------------------------------------

        interface ScatterPeerDistributionResponse {
          indicator_key: string;
          place_id: string;
          focal_value: number | null;
          peer_place_values: { place_id: string; value: number | null }[];
          peer_count: number;
          unit: string;
          period: string;
        }

        interface ScatterPoint {
          place_id: string;
          x_value: number;
          y_value: number;
          is_focal: boolean;
        }

        async function renderScatterPlotBlock(
          host: HTMLElement,
          block: { type: string; [k: string]: unknown },
          apiBase: string,
        ) {
          const xKey = asString(block.x_indicator_key);
          const yKey = asString(block.y_indicator_key);
          const caption = asStringOrUndef(block.caption);
          const scatterPlaceId = asStringOrUndef(block.place_id) ?? placeId;
          if (!xKey || !yKey) {
            showBlockError(
              host,
              "Scatter plot missing x_indicator_key or y_indicator_key.",
            );
            return;
          }
          if (!scatterPlaceId) {
            showBlockError(host, "Scatter plot missing place_id.");
            return;
          }
          let xResp: ScatterPeerDistributionResponse;
          let yResp: ScatterPeerDistributionResponse;
          try {
            [xResp, yResp] = await Promise.all([
              postJSON<ScatterPeerDistributionResponse>(
                "/v1/tools/get_peer_distribution",
                { indicator_key: xKey, place_id: scatterPlaceId },
                apiBase,
              ),
              postJSON<ScatterPeerDistributionResponse>(
                "/v1/tools/get_peer_distribution",
                { indicator_key: yKey, place_id: scatterPlaceId },
                apiBase,
              ),
            ]);
          } catch (err) {
            showBlockError(
              host,
              "Could not load scatter data: " +
                (err instanceof Error ? err.message : String(err)),
            );
            return;
          }
          const xMap = new Map<string, number | null>();
          for (const p of xResp.peer_place_values ?? []) {
            xMap.set(p.place_id, p.value);
          }
          const points: ScatterPoint[] = [];
          for (const p of yResp.peer_place_values ?? []) {
            const xv = xMap.get(p.place_id);
            if (typeof xv !== "number" || typeof p.value !== "number") continue;
            points.push({
              place_id: p.place_id,
              x_value: xv,
              y_value: p.value,
              is_focal: p.place_id === scatterPlaceId,
            });
          }
          if (points.length === 0) {
            showBlockError(host, "No scatter data available.");
            return;
          }
          const { renderScatterPlot } = await import("../lib/chart");
          const svg = renderScatterPlot(
            {
              points,
              focal_place_id: scatterPlaceId,
              x_label: prettyKey(xKey),
              y_label: prettyKey(yKey),
              caption,
            },
            { containerWidth: host.clientWidth || 480 },
          );
          if (!svg) {
            showBlockError(host, "No scatter data available.");
            return;
          }
          const figure = document.createElement("figure");
          figure.className = "scatter-plot-block";
          const chartDiv = document.createElement("div");
          chartDiv.className = "chart";
          chartDiv.innerHTML = svg;
          figure.appendChild(chartDiv);
          if (caption) {
            const figcaption = document.createElement("figcaption");
            figcaption.textContent = caption;
            figure.appendChild(figcaption);
          }
          host.appendChild(figure);
        }

        // sub-area-table -----------------------------------------------------

        interface SubAreaEntry {
          place_id: string;
          name: string;
          value: number | null;
          percentile?: number | null;
        }

        function asSubAreas(v: unknown): SubAreaEntry[] {
          if (!Array.isArray(v)) return [];
          return v
            .map((entry): SubAreaEntry | null => {
              if (typeof entry !== "object" || entry === null) return null;
              const r = entry as Record<string, unknown>;
              const place_id =
                typeof r.place_id === "string" ? r.place_id : "";
              const name = typeof r.name === "string" ? r.name : "";
              const value =
                typeof r.value === "number" && Number.isFinite(r.value)
                  ? r.value
                  : null;
              const percentile =
                typeof r.percentile === "number" &&
                Number.isFinite(r.percentile)
                  ? r.percentile
                  : null;
              return { place_id, name, value, percentile };
            })
            .filter((e): e is SubAreaEntry => e !== null);
        }

        function renderSubAreaTableBlock(
          host: HTMLElement,
          block: { type: string; [k: string]: unknown },
        ) {
          const subAreas = asSubAreas(block.sub_areas);
          if (subAreas.length === 0) {
            showBlockError(
              host,
              "Sub-area table missing sub_areas data.",
            );
            return;
          }
          const hasPercentile = subAreas.some(
            (e) => e.percentile !== null && e.percentile !== undefined,
          );
          const period = asStringOrUndef(block.period);
          const caption = asStringOrUndef(block.caption);
          const parentValue =
            typeof block.parent_value === "number" &&
            Number.isFinite(block.parent_value)
              ? (block.parent_value as number)
              : null;
          const parentLabel = asStringOrUndef(block.parent_label);

          const container = document.createElement("div");
          container.className =
            "sub-area-table-block answer-block";

          if (caption) {
            const cap = document.createElement("p");
            cap.className = "sub-area-caption";
            cap.textContent = caption;
            container.appendChild(cap);
          }

          const table = document.createElement("table");
          table.className = "sub-area-table";
          const thead = document.createElement("thead");
          const headRow = document.createElement("tr");
          const thName = document.createElement("th");
          thName.textContent = "Neighbourhood";
          headRow.appendChild(thName);
          const thValue = document.createElement("th");
          thValue.textContent = "Value";
          headRow.appendChild(thValue);
          if (hasPercentile) {
            const thPct = document.createElement("th");
            thPct.textContent = "Percentile";
            headRow.appendChild(thPct);
          }
          thead.appendChild(headRow);
          table.appendChild(thead);

          const tbody = document.createElement("tbody");
          for (const entry of subAreas) {
            const tr = document.createElement("tr");
            const tdName = document.createElement("td");
            tdName.textContent = entry.name || entry.place_id;
            tr.appendChild(tdName);
            const tdValue = document.createElement("td");
            tdValue.textContent = formatValue(entry.value);
            tdValue.className = "num";
            tr.appendChild(tdValue);
            if (hasPercentile) {
              const tdPct = document.createElement("td");
              tdPct.className = "num";
              tdPct.textContent =
                entry.percentile !== null && entry.percentile !== undefined
                  ? "p" + Math.round(entry.percentile)
                  : "—";
              tr.appendChild(tdPct);
            }
            tbody.appendChild(tr);
          }
          table.appendChild(tbody);

          if (parentValue !== null) {
            const tfoot = document.createElement("tfoot");
            const tr = document.createElement("tr");
            tr.className = "parent-row";
            const tdLabel = document.createElement("td");
            tdLabel.textContent =
              parentLabel ?? "Parent average";
            tr.appendChild(tdLabel);
            const tdValue = document.createElement("td");
            tdValue.textContent = formatValue(parentValue);
            tdValue.className = "num";
            tr.appendChild(tdValue);
            if (hasPercentile) {
              const tdPct = document.createElement("td");
              tdPct.textContent = "";
              tr.appendChild(tdPct);
            }
            tfoot.appendChild(tr);
            table.appendChild(tfoot);
          }

          container.appendChild(table);

          if (period) {
            const periodP = document.createElement("p");
            periodP.className = "sub-area-period text-muted text-small";
            periodP.textContent = period;
            container.appendChild(periodP);
          }

          host.appendChild(container);
        }

        function renderBlockInto(
          target: HTMLElement,
          block: { type: string; [k: string]: unknown },
        ) {
          const host = document.createElement("div");
          host.className = "answer-block block-" + block.type;
          switch (block.type) {
            case "text": {
              // Server schema (TextBlock) names this field `markdown`, not `text`.
              const text =
                typeof block.markdown === "string" ? block.markdown : "";
              const div = document.createElement("div");
              div.className = "answer-text";
              div.innerHTML = renderMarkdown(text);
              host.appendChild(div);
              break;
            }
            case "insight-callout": {
              const severity =
                typeof block.severity === "string" ? block.severity : "notable";
              const headline =
                typeof block.headline === "string" ? block.headline : "";
              const evidence =
                typeof block.evidence === "string" ? block.evidence : "";
              const callout = document.createElement("div");
              callout.className =
                "insight-callout severity-" + severity;
              const h = document.createElement("p");
              h.className = "callout-headline";
              h.textContent = headline;
              callout.appendChild(h);
              if (evidence) {
                const e = document.createElement("p");
                e.className = "callout-evidence";
                e.textContent = evidence;
                callout.appendChild(e);
              }
              host.appendChild(callout);
              break;
            }
            case "indicator-card": {
              renderIndicatorCard(host, block, apiBase);
              break;
            }
            case "trend-chart": {
              renderTrendChartBlock(host, block, apiBase);
              break;
            }
            case "compare-chart": {
              renderCompareChartBlock(host, block, apiBase);
              break;
            }
            case "map": {
              renderMapBlock(host, block, apiBase);
              break;
            }
            case "organisations": {
              renderOrganisationsBlock(host, block, apiBase);
              break;
            }
            case "distribution-chart": {
              renderDistributionChartBlock(host, block, apiBase);
              break;
            }
            case "composition-chart": {
              renderCompositionChartBlock(host, block);
              break;
            }
            case "bar-chart": {
              renderBarChartBlock(host, block);
              break;
            }
            case "scatter-plot": {
              renderScatterPlotBlock(host, block, apiBase);
              break;
            }
            case "sub-area-table": {
              renderSubAreaTableBlock(host, block);
              break;
            }
            default: {
              const ph = document.createElement("div");
              ph.className = "block-placeholder block-unknown";
              ph.textContent = "Unknown block: " + JSON.stringify(block);
              host.appendChild(ph);
            }
          }
          target.appendChild(host);
        }

        function renderBlock(block: { type: string; [k: string]: unknown }) {
          renderBlockInto(surface, block);
        }

        function renderSources(sources: { source_id?: string; source_label?: string; publisher?: string; dataset_url?: string }[]) {
          const footer = document.getElementById("answer-sources");
          if (!footer) return;
          footer.innerHTML = "";
          if (sources.length === 0) return;
          const sec = document.createElement("section");
          sec.className = "sources-footer";
          const h = document.createElement("h3");
          h.textContent = "Sources";
          sec.appendChild(h);
          const ul = document.createElement("ul");
          for (const ref of sources) {
            const li = document.createElement("li");
            if (ref.dataset_url) {
              const a = document.createElement("a");
              a.href = ref.dataset_url;
              a.target = "_blank";
              a.rel = "noopener";
              a.textContent = ref.source_label || ref.source_id || "source";
              li.appendChild(a);
            } else {
              li.textContent = ref.source_label || ref.source_id || "source";
            }
            if (ref.publisher) {
              const span = document.createElement("span");
              span.className = "source-publisher";
              span.textContent = " · " + ref.publisher;
              li.appendChild(span);
            }
            ul.appendChild(li);
          }
          sec.appendChild(ul);
          footer.appendChild(sec);
        }

        function renderError(message: string) {
          surface.innerHTML = "";
          const div = document.createElement("div");
          div.className = "answer-error";
          const p = document.createElement("p");
          p.textContent = "Sorry — something went wrong: " + message;
          div.appendChild(p);
          const retry = document.createElement("a");
          retry.href = window.location.pathname + window.location.search;
          retry.textContent = "Retry";
          div.appendChild(retry);
          surface.appendChild(div);
        }

        const body: Record<string, unknown> = { query };
        if (placeId) body.place_id = placeId;

        // ── Multi-turn conversation state ──────────────────────────
        let conversationId: string | null = null;
        let isStreaming = false;

        // Clear the "Thinking…" status placeholder once first event arrives.
        let firstEvent = true;

        function clearThinking() {
          if (!firstEvent) return;
          const s = surface.querySelector(".answer-status");
          if (s) s.remove();
          firstEvent = false;
        }

        // ── Follow-up input ─────────────────────────────────────────
        let followUpForm: HTMLFormElement | null = null;

        function removeFollowUpForm() {
          if (followUpForm) {
            followUpForm.remove();
            followUpForm = null;
          }
        }

        function renderFollowUpForm() {
          removeFollowUpForm();
          if (!conversationId) return;
          followUpForm = document.createElement("form");
          followUpForm.className = "follow-up-form";
          const input = document.createElement("input");
          input.type = "text";
          input.className = "follow-up-input";
          input.placeholder = "Ask a follow-up…";
          input.autocomplete = "off";
          input.required = true;
          const btn = document.createElement("button");
          btn.type = "submit";
          btn.className = "follow-up-submit";
          btn.textContent = "Ask";
          followUpForm.append(input, btn);
          followUpForm.addEventListener("submit", (e) => {
            e.preventDefault();
            if (isStreaming || !input.value.trim()) return;
            startFollowUp(input.value.trim());
          });
          surface.appendChild(followUpForm);
          input.focus();
        }

        function startFollowUp(q: string) {
          if (!conversationId) return;
          isStreaming = true;
          removeFollowUpForm();

          // Create a new turn container
          const turn = document.createElement("div");
          turn.className = "conversation-turn";
          const qEl = document.createElement("blockquote");
          qEl.className = "turn-question";
          qEl.textContent = q;
          turn.appendChild(qEl);
          const aEl = document.createElement("div");
          aEl.className = "turn-answer";
          turn.appendChild(aEl);
          surface.appendChild(turn);

          // Reset step-tracking state for this turn
          stepsEl = null;
          firstEvent = true;

          const fuBody: Record<string, unknown> = { query: q };
          if (placeId) fuBody.place_id = placeId;
          fuBody.conversation_id = conversationId;

          // Temporarily redirect renderBlock to render into the turn's answer area
          const origRenderBlock = renderBlock;
          const origRenderError = renderError;
          renderBlock = function (block: AnswerBlock) {
            renderBlockInto(aEl, block);
          };
          renderError = function (message: string) {
            const div = document.createElement("div");
            div.className = "answer-error";
            div.textContent = "Sorry — something went wrong: " + message;
            aEl.appendChild(div);
          };

          streamAsk(apiBase + "/v1/ask", fuBody, (event) => {
            switch (event.type) {
              case "conversation":
                // Update conversation ID if server sends a new one
                conversationId = event.conversation_id;
                break;
              case "status":
                clearThinking();
                pushStep(friendlyStep(event.message));
                break;
              case "block":
                clearThinking();
                renderBlock(event.block);
                break;
              case "sources":
                renderSources(event.sources);
                break;
              case "done":
                clearThinking();
                finishSteps();
                isStreaming = false;
                renderBlock = origRenderBlock;
                renderError = origRenderError;
                renderFollowUpForm();
                break;
              case "error":
                renderError(event.message);
                isStreaming = false;
                renderBlock = origRenderBlock;
                renderError = origRenderError;
                renderFollowUpForm();
                break;
            }
          }).catch((err) => {
            renderError(err instanceof Error ? err.message : String(err));
            isStreaming = false;
            renderBlock = origRenderBlock;
            renderError = origRenderError;
            renderFollowUpForm();
          });
        }

        // ── Initial (first) question stream ────────────────────────
        streamAsk(apiBase + "/v1/ask", body, (event) => {
          switch (event.type) {
            case "conversation":
              conversationId = event.conversation_id;
              break;
            case "status":
              clearThinking();
              pushStep(friendlyStep(event.message));
              break;
            case "block":
              clearThinking();
              renderBlock(event.block);
              break;
            case "sources":
              renderSources(event.sources);
              break;
            case "done":
              clearThinking();
              finishSteps();
              isStreaming = false;
              renderFollowUpForm();
              break;
            case "error":
              renderError(event.message);
              break;
          }
        }).catch((err) => {
          renderError(err instanceof Error ? err.message : String(err));
        });
        isStreaming = true;
      }
