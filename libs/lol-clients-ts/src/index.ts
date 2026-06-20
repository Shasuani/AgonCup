/**
 * @lol/clients-ts — framework-agnostic, fetch-based TypeScript client for the
 * LeagueOfLeagues backend HTTP API (services/backend, mounted at `/api/v1`).
 *
 * ============================================================================
 *  HAND-WRITTEN FOR NOW — SHOULD LATER BE GENERATED FROM THE BACKEND OPENAPI.
 * ============================================================================
 *  This package is currently hand-authored against the FastAPI routers under
 *  `services/backend/src/app/<resource>/router.py`. The request/response
 *  interfaces in `types.ts` mirror the backend Pydantic DTOs by hand.
 *
 *  Target pipeline (later phase, PLAN.md §5 libs):
 *    backend (FastAPI) --> openapi.json --> `make gen-clients` --> this package
 *
 *  When `make gen-clients` lands it should REPLACE the hand-written model
 *  layer (`types.ts`) with generated models and keep the ergonomic
 *  `createClient` factory (or regenerate an equivalent). Until then, when a
 *  backend router changes shape, update `types.ts` + `client.ts` here in
 *  lockstep and keep `tsc --noEmit` green.
 *
 *  Consumers (frontend/desktop) MUST go through this client — no ad-hoc fetch
 *  in screens. Auth + the acting guild are injected via `getToken` /
 *  `getActingGuild`, sourced from the shell's providers.
 * ============================================================================
 */

export { createClient } from './client.js';
export type {
  AccountsApi,
  ApiClient,
  AuthApi,
  FederationApi,
  LifecycleApi,
  OrchestrationApi,
  ResultsApi,
  TeamsApi,
  TournamentsApi,
} from './client.js';

export { ApiError, isApiError } from './errors.js';
export type { ApiErrorBody } from './errors.js';

export { API_BASE_PATH } from './http.js';
export type { ClientConfig, RequestOptions, RequestRunner } from './http.js';

export type * from './types.js';
