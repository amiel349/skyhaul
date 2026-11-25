/** After login go only to a local path: one leading slash, not `//host` or `/\host`. */
export function safeNext(value: unknown): string {
  if (typeof value !== "string") return "/";
  return /^\/(?![/\\])/.test(value) ? value : "/";
}
