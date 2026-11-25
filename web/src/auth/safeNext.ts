/**
 * After login go only to a local path: one leading slash, not `//host` or `/\host`, and no
 * control characters. A browser drops tab, CR and LF inside a URL, so `/<TAB>/evil.example` would
 * read as `//evil.example` once parsed.
 */
export function safeNext(value: unknown): string {
  if (typeof value !== "string") return "/";
  if (/[\u0000-\u001f\u007f]/.test(value)) return "/";
  return /^\/(?![/\\])/.test(value) ? value : "/";
}
