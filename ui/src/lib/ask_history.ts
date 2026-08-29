export interface AskHistoryEntry {
  query: string;
  placeId?: string;
  completedAt: string;
}

const STORAGE_KEY = "soundings.ask-history.v1";
const MAX_HISTORY = 8;

function normaliseQuery(query: string): string {
  return query.trim().replace(/\s+/g, " ").toLowerCase();
}

export function readAskHistory(storage: Storage = window.localStorage): AskHistoryEntry[] {
  try {
    const raw = storage.getItem(STORAGE_KEY);
    if (!raw) return [];
    const parsed: unknown = JSON.parse(raw);
    if (!Array.isArray(parsed)) return [];
    return parsed
      .filter((item): item is AskHistoryEntry => {
        if (typeof item !== "object" || item === null) return false;
        const row = item as Record<string, unknown>;
        return (
          typeof row.query === "string" &&
          typeof row.completedAt === "string" &&
          (row.placeId === undefined || typeof row.placeId === "string")
        );
      })
      .slice(0, MAX_HISTORY);
  } catch {
    return [];
  }
}

export function rememberQuestion(
  query: string,
  placeId?: string,
  storage: Storage = window.localStorage,
): void {
  const trimmed = query.trim();
  if (!trimmed) return;

  const key = normaliseQuery(trimmed) + "|" + (placeId ?? "");
  const existing = readAskHistory(storage).filter(
    (entry) =>
      normaliseQuery(entry.query) + "|" + (entry.placeId ?? "") !== key,
  );
  const next: AskHistoryEntry[] = [
    {
      query: trimmed,
      placeId,
      completedAt: new Date().toISOString(),
    },
    ...existing,
  ].slice(0, MAX_HISTORY);

  try {
    storage.setItem(STORAGE_KEY, JSON.stringify(next));
  } catch {
    // Storage can be unavailable in private/restricted browser contexts.
  }
}

export function recentQuestions(
  placeId?: string,
  storage: Storage = window.localStorage,
): AskHistoryEntry[] {
  const entries = readAskHistory(storage);
  if (!placeId) return entries;
  return entries.filter((entry) => entry.placeId === placeId);
}

export function askHistoryHref(entry: AskHistoryEntry): string {
  const params = new URLSearchParams({ q: entry.query });
  if (entry.placeId) params.set("place_id", entry.placeId);
  return "/ask?" + params.toString();
}
