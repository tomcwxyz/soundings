import { marked } from "marked";

// Marked deliberately does not sanitise HTML. Limit the markup the renderer
// creates instead of trusting model output (or previously cached answers).
function escapeHTML(text: string): string {
  return text.replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

function safeURL(href: string): boolean {
  const value = href.trim();
  if (/[\u0000-\u001f\u007f]/.test(value)) return false;
  return /^(https?:\/\/|mailto:|\/(?!\/)|#)/i.test(value);
}

const renderer = new marked.Renderer();

renderer.html = ({ text }) => escapeHTML(text);

renderer.link = function ({ href, title, tokens }) {
  const label = this.parser.parseInline(tokens);
  if (!safeURL(href)) return label;
  const tooltip = title ? ` title="${escapeHTML(title)}"` : "";
  return `<a href="${escapeHTML(href)}"${tooltip} rel="noopener noreferrer">${label}</a>`;
};

renderer.image = ({ href, title, text }) => {
  if (!safeURL(href)) return escapeHTML(text);
  const tooltip = title ? ` title="${escapeHTML(title)}"` : "";
  return `<img src="${escapeHTML(href)}" alt="${escapeHTML(text)}"${tooltip}>`;
};

export function renderSafeMarkdown(text: string): string {
  return marked.parse(text, {
    renderer,
    async: false,
    gfm: true,
    breaks: true,
  }) as string;
}
