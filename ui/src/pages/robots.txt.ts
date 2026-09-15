import type { APIRoute } from "astro";

export const GET: APIRoute = ({ url }) => {
  const body = [
    "User-agent: *",
    "Allow: /",
    `Sitemap: ${new URL("/sitemap.xml", url.origin).toString()}`,
    "",
  ].join("\n");

  return new Response(body, {
    headers: {
      "Content-Type": "text/plain; charset=utf-8",
      "Cache-Control": "public, max-age=3600",
    },
  });
};
