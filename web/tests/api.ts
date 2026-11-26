// Type helpers for fake endpoints: the body types come from the generated OpenAPI types, so a
// contract change breaks `pnpm typecheck` in every fake that no longer matches.
import type { paths } from "../src/api/types";

type Method = "get" | "post";

/** The JSON body of the success response of `method` on `path` (200 unless `S` says 202). */
export type Ok<P extends keyof paths, M extends Method, S extends 200 | 202 = 200> = paths[P][M] extends {
  responses: { [K in S]: { content: { "application/json": infer B } } };
}
  ? B
  : never;

/** The JSON request body of `method` on `path`. */
export type Body<P extends keyof paths, M extends Method> = paths[P][M] extends {
  requestBody: { content: { "application/json": infer B } };
}
  ? B
  : never;

/** Absolute URL of an API path, on the origin the page (and the fake worker) runs on. */
export const apiUrl = (path: string): string => new URL(`/api/v1${path}`, window.location.origin).toString();
