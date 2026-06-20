/**
 * @lol/clients-ts — TypeScript client for the backend HTTP API.
 *
 * ============================================================================
 *  GENERATED CODE — DO NOT HAND-EDIT  (PLAN.md §5 libs)
 * ============================================================================
 *  The real contents of this package are GENERATED from the backend's OpenAPI
 *  schema by `make gen-clients`. Hand edits are clobbered on the next codegen.
 *  The desktop client / frontend consume the typed API surface from here so
 *  request/response types stay in lockstep with the FastAPI backend.
 *
 *  Pipeline (later phase):
 *    backend (FastAPI) --> openapi.json --> `make gen-clients` --> this package.
 *
 *  Phase-0 STATUS: this is a HAND-WRITTEN PLACEHOLDER so the package compiles
 *  under `tsc --noEmit`. It defines one trivial type and is replaced wholesale
 *  by the generator.
 * ============================================================================
 *
 * TODO(gen-clients): replace this entire file with output from `make gen-clients`
 *   (backend OpenAPI -> typed models + operations). Until the generator and the
 *   backend OpenAPI schema exist, the placeholder below stands in.
 */

/**
 * Placeholder marker for the generated client. Lets downstream packages import
 * `@lol/clients-ts` and `tsc` succeed before codegen exists. Replaced wholesale
 * by `make gen-clients`.
 */
export interface GeneratedClientPlaceholder {
  /** Discriminator so accidental use of the placeholder is obvious at runtime. */
  readonly __generated: false;
  /** Matches the OpenAPI `info.version` once codegen is wired up. */
  readonly apiVersion: string;
}

/** Marker constant; superseded by the generated client surface. */
export const GENERATED_CLIENT_PLACEHOLDER: GeneratedClientPlaceholder = {
  __generated: false,
  apiVersion: '0.0.0',
};
