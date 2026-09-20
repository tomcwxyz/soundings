import type { APIRoute } from "astro";

import { siteOrigin } from "../lib/site";

export const GET: APIRoute = ({ url }) => {
  const body = [
    "User-agent: *",
    "Allow: /",
    `Sitemap: ${new URL("/sitemap.xml", siteOrigin(url.origin)).toString()}`,
    "",
  ].join("\n");

  return new Response(body, {
    headers: {
      "Content-Type": "text/plain; charset=utf-8",
      "Cache-Control": "public, max-age=3600",
    },
  });
};
