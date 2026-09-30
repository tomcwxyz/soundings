// Canonical public origin for SEO and social-sharing URLs.
//
// Reads `SOUNDINGS_PUBLIC_SITE_URL` from `process.env` at request time for
// the same reason as `apiBase()` in ./api — `import.meta.env` resolves at
// build time and would freeze the value into the bundle.
//
// This exists because the @astrojs/node standalone adapter builds
// `Astro.url` from the socket it is bound to, not from the forwarded Host
// header. Behind Caddy + cloudflared the request origin is therefore
// `http://localhost`, which is useless in a canonical tag, a sitemap or an
// Open Graph image URL. No proxy configuration fixes that, so the public
// origin has to be supplied explicitly.
//
// Falls back to the request origin so `npm run dev` needs no configuration.

export function siteOrigin(requestOrigin: string): string {
  const configured =
    typeof process !== "undefined"
      ? process.env.SOUNDINGS_PUBLIC_SITE_URL
      : undefined;

  if (configured === undefined || configured.length === 0) {
    return requestOrigin;
  }

  try {
    // Normalise: a trailing slash or stray path must not leak into the
    // URLs we build from this.
    return new URL(configured).origin;
  } catch {
    // A misconfigured value should degrade to dev-correct metadata rather
    // than 500 every page that renders a canonical tag.
    return requestOrigin;
  }
}
