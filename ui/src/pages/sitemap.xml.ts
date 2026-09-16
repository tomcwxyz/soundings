import type { APIRoute } from "astro";

const publicRoutes = [
  "/",
  "/explore",
  "/compare",
  "/ask",
  "/corpus",
  "/about",
] as const;

const escapeXml = (value: string) =>
  value
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&apos;");

export const GET: APIRoute = ({ url }) => {
  const entries = publicRoutes
    .map(
      (path) =>
        `  <url><loc>${escapeXml(new URL(path, url.origin).toString())}</loc></url>`,
    )
    .join("\n");

  const body = [
    '<?xml version="1.0" encoding="UTF-8"?>',
    '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">',
    entries,
    "</urlset>",
    "",
  ].join("\n");

  return new Response(body, {
    headers: {
      "Content-Type": "application/xml; charset=utf-8",
      "Cache-Control": "public, max-age=3600",
    },
  });
};
