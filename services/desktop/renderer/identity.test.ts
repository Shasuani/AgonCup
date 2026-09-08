/**
 * Unit tests for the "which account am I right now?" picker (node:test; pure,
 * no React rendering). The bug this guards: with two linked accounts the UI
 * showed the LIVE account's name next to the FIRST linked account's rank.
 */
import assert from 'node:assert/strict';
import { test } from 'node:test';

import type { LinkedAccount } from './bridge';
import { pickIdentity } from './identity';

const alpha: LinkedAccount = { accountId: 'a', gameName: 'Alpha', tagLine: 'EUNE', region: 'eun1', verified: true, rankText: 'Gold II · 40 LP' };
const zeta: LinkedAccount = { accountId: 'z', gameName: 'Zeta', tagLine: '1234', region: 'euw1', verified: true, rankText: 'Silver I · 10 LP' };
const accounts = [alpha, zeta]; // backend order: verified first, then by name
const user = { userId: 'u', discordUserId: 'd', displayName: 'Kostas' };

test('League open: the signed-in account wins, and its OWN rank comes with it', () => {
  const id = pickIdentity({ ok: true, gameName: 'zeta', tagLine: '1234', region: 'euw1', profileIconId: 7, summonerLevel: 300 }, { ok: true, gameName: 'Alpha', tagLine: 'EUNE', profileIconId: 1, summonerLevel: 50 }, accounts, user);
  assert.equal(id.source, 'live');
  assert.equal(id.riotId, 'zeta#1234');
  assert.equal(id.account?.accountId, 'z');
  assert.equal(id.account?.rankText, 'Silver I · 10 LP');
  assert.equal(id.region, 'euw1');
  assert.equal(id.profileIconId, 7);
  assert.equal(id.level, 300);
});

test('League closed: the last account this device saw, not the first in the list', () => {
  const id = pickIdentity({ ok: false, reason: 'lcu_unavailable' }, { ok: true, gameName: 'Zeta', tagLine: '1234', region: 'euw1', profileIconId: 7, summonerLevel: 300 }, accounts, user);
  assert.equal(id.source, 'remembered');
  assert.equal(id.riotId, 'Zeta#1234');
  assert.equal(id.account?.accountId, 'z');
  assert.equal(id.profileIconId, 7);
  assert.equal(id.level, 300);
});

test('nothing remembered yet: the first linked account, with no borrowed icon', () => {
  const id = pickIdentity(null, { ok: true }, accounts, user);
  assert.equal(id.source, 'linked');
  assert.equal(id.riotId, 'Alpha#EUNE');
  assert.equal(id.account?.accountId, 'a');
  assert.equal(id.profileIconId, null);
  assert.equal(id.level, null);
});

test('signed in to an account that is not linked: show it, but no rank to claim', () => {
  const id = pickIdentity({ ok: true, gameName: 'Smurf', tagLine: 'EUW', region: 'euw1', profileIconId: 3 }, null, accounts, user);
  assert.equal(id.source, 'live');
  assert.equal(id.riotId, 'Smurf#EUW');
  assert.equal(id.account, null);
  assert.equal(id.region, 'euw1');
});

test('no accounts, League closed, nothing remembered: falls back to the Discord name', () => {
  const id = pickIdentity(null, null, [], user);
  assert.equal(id.source, 'none');
  assert.equal(id.riotId, 'Kostas');
  assert.equal(id.account, null);
  assert.equal(id.region, null);
});
