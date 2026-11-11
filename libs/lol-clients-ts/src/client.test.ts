/**
 * Transport-level tests for the hand-written client.
 *
 * We mock the injected `fetch` and assert that a representative tenant-scoped
 * method and an auth method build the right URL + headers (Bearer token,
 * X-Guild-Id) and that JSON is parsed on 2xx / an {@link ApiError} is thrown on
 * non-2xx. Importing the module does NO network IO.
 */

import { describe, expect, it, vi } from 'vitest';

import { createClient } from './client.js';
import { ApiError, isApiError } from './errors.js';

const BASE = 'https://api.example.test/';

/** Build a fake fetch returning a JSON response with the given status. */
function jsonResponse(status: number, body: unknown): Response {
  return new Response(body === undefined ? '' : JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });
}

describe('createClient — tenant-scoped request', () => {
  it('builds /api/v1 URL with Bearer token + X-Guild-Id and parses JSON', async () => {
    const fetchMock = vi
      .fn<typeof fetch>()
      .mockResolvedValue(
        jsonResponse(200, [{ id: 't1', name: 'Spring', status: 'open' }]),
      );

    const api = createClient({
      baseUrl: BASE,
      getToken: () => 'tok-abc',
      getActingGuild: () => 'guild-42',
      fetch: fetchMock,
    });

    const result = await api.tournaments.list();

    expect(result).toEqual([{ id: 't1', name: 'Spring', status: 'open' }]);
    expect(fetchMock).toHaveBeenCalledTimes(1);

    const [url, init] = fetchMock.mock.calls[0]!;
    expect(url).toBe('https://api.example.test/api/v1/tournaments');
    expect(init?.method).toBe('GET');

    const headers = init?.headers as Record<string, string>;
    expect(headers['Authorization']).toBe('Bearer tok-abc');
    expect(headers['X-Guild-Id']).toBe('guild-42');
  });

  it('reads a tournament bracket from /tournaments/{id}/bracket', async () => {
    const body = {
      tournament: { id: 't1', name: 'Spring', status: 'running' },
      matches: [{ id: 'm1', round: 1, slot: 0, team_a_id: 'a', team_a_name: 'Alpha', team_b_id: 'b', team_b_name: 'Beta', winner_team_id: null, status: 'pending' }],
    };
    const fetchMock = vi.fn<typeof fetch>().mockResolvedValue(jsonResponse(200, body));
    const api = createClient({ baseUrl: BASE, getActingGuild: () => 'guild-42', fetch: fetchMock });

    const result = await api.tournaments.bracket('t 1');

    expect(result).toEqual(body);
    const [url, init] = fetchMock.mock.calls[0]!;
    expect(url).toBe('https://api.example.test/api/v1/tournaments/t%201/bracket');
    expect(init?.method).toBe('GET');
    expect((init?.headers as Record<string, string>)['X-Guild-Id']).toBe('guild-42');
  });

  it('serializes the body and encodes path ids on POST', async () => {
    const fetchMock = vi
      .fn<typeof fetch>()
      .mockResolvedValue(
        jsonResponse(200, {
          id: 'r1',
          tournament_id: 't 1',
          team_id: 'team-9',
          status: 'registered',
        }),
      );

    const api = createClient({
      baseUrl: BASE,
      getToken: async () => 'tok-async',
      getActingGuild: () => 'guild-7',
      fetch: fetchMock,
    });

    await api.tournaments.register('t 1', { team_id: 'team-9' });

    const [url, init] = fetchMock.mock.calls[0]!;
    expect(url).toBe('https://api.example.test/api/v1/tournaments/t%201/registrations');
    expect(init?.method).toBe('POST');
    expect(init?.body).toBe(JSON.stringify({ team_id: 'team-9' }));

    const headers = init?.headers as Record<string, string>;
    expect(headers['Content-Type']).toBe('application/json');
    expect(headers['Authorization']).toBe('Bearer tok-async');
  });
});

describe('createClient — auth routes', () => {
  it('refresh does NOT attach Authorization or X-Guild-Id', async () => {
    const fetchMock = vi
      .fn<typeof fetch>()
      .mockResolvedValue(
        jsonResponse(200, {
          access_token: 'a',
          refresh_token: 'b',
          token_type: 'bearer',
        }),
      );

    const api = createClient({
      baseUrl: BASE,
      getToken: () => 'should-not-be-used',
      getActingGuild: () => 'guild-x',
      fetch: fetchMock,
    });

    const pair = await api.auth.refresh({ refresh_token: 'r0' });
    expect(pair.access_token).toBe('a');

    const [url, init] = fetchMock.mock.calls[0]!;
    expect(url).toBe('https://api.example.test/api/v1/auth/refresh');
    const headers = init?.headers as Record<string, string>;
    expect(headers['Authorization']).toBeUndefined();
    expect(headers['X-Guild-Id']).toBeUndefined();
  });

  it('exposes the login redirect URL without calling fetch', () => {
    const fetchMock = vi.fn<typeof fetch>();
    const api = createClient({ baseUrl: BASE, fetch: fetchMock });
    expect(api.auth.loginUrl()).toBe('https://api.example.test/api/v1/auth/login');
    expect(fetchMock).not.toHaveBeenCalled();
  });
});

describe('createClient — error handling', () => {
  it('throws a typed ApiError with status + FastAPI detail on non-2xx', async () => {
    // Return a FRESH response per call — a Response body can only be read once.
    const fetchMock = vi
      .fn<typeof fetch>()
      .mockImplementation(async () => jsonResponse(404, { detail: 'tournament not found' }));

    const api = createClient({ baseUrl: BASE, getToken: () => 't', fetch: fetchMock });

    await expect(api.tournaments.get('missing')).rejects.toThrowError(ApiError);

    try {
      await api.tournaments.get('missing');
      expect.unreachable('should have thrown');
    } catch (err) {
      expect(isApiError(err)).toBe(true);
      if (isApiError(err)) {
        expect(err.status).toBe(404);
        expect(err.detail).toBe('tournament not found');
        expect(err.message).toBe('tournament not found');
      }
    }
  });

  it('resolves 204 No Content to undefined (void routes)', async () => {
    const fetchMock = vi
      .fn<typeof fetch>()
      .mockResolvedValue(new Response(null, { status: 204 }));

    const api = createClient({ baseUrl: BASE, getToken: () => 't', fetch: fetchMock });
    await expect(api.teams.leave('team-1')).resolves.toBeUndefined();

    const [url, init] = fetchMock.mock.calls[0]!;
    expect(url).toBe('https://api.example.test/api/v1/teams/team-1/leave');
    expect(init?.method).toBe('POST');
  });

  it('omits headers when getToken/getActingGuild are not provided', async () => {
    const fetchMock = vi
      .fn<typeof fetch>()
      .mockResolvedValue(jsonResponse(200, []));

    const api = createClient({ baseUrl: BASE, fetch: fetchMock });
    await api.teams.list();

    const headers = fetchMock.mock.calls[0]![1]?.headers as Record<string, string>;
    expect(headers['Authorization']).toBeUndefined();
    expect(headers['X-Guild-Id']).toBeUndefined();
  });
});
