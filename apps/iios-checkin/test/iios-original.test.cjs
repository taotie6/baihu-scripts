'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');

process.env.IIOS_UNIT_TEST = '1';

const preferences = new Map();
globalThis.$prefs = {
  valueForKey(key) { return preferences.get(key) || ''; },
  setValueForKey(value, key) { preferences.set(key, String(value)); return true; },
};
globalThis.$task = { fetch() { throw new Error('unexpected real request'); } };

const { checkinSession, rememberRefreshedToken } = require('../main/iios-original.js');

function fakeRuntime(decodedPayload) {
  return {
    async encode() { return { s: 'request-signature', d: 'encoded-request' }; },
    async decode() { return { d: JSON.stringify(decodedPayload) }; },
  };
}

test('signed 403 saying the account already checked in is successful', async () => {
  const result = await checkinSession(
    { label: 'account-2', token: 'test-token', cookie: '', userAgent: 'test-agent' },
    {
      runtime: fakeRuntime({ message: '今日已签到' }),
      async request() {
        return {
          status: 403,
          headers: { 'x-signature': 'response-signature' },
          body: 'encoded-response',
        };
      },
    },
  );

  assert.equal(result.ok, true);
  assert.equal(result.alreadyCheckedIn, true);
});

test('an unsigned generic 403 remains a failure', async () => {
  const result = await checkinSession(
    { label: 'expired', token: 'test-token', cookie: '', userAgent: 'test-agent' },
    {
      runtime: fakeRuntime({ message: '今日已签到' }),
      async request() {
        return { status: 403, headers: {}, body: 'Forbidden' };
      },
    },
  );

  assert.equal(result.ok, false);
  assert.match(result.error, /^HTTP 403/);
});

test('a Cloudflare challenge produces an actionable error', async () => {
  const result = await checkinSession(
    { label: 'cloudflare', token: 'test-token', cookie: '', userAgent: 'test-agent' },
    {
      runtime: fakeRuntime({}),
      async request() {
        return {
          status: 403,
          headers: { 'cf-mitigated': 'challenge' },
          body: '<!DOCTYPE html><title>Just a moment...</title>',
        };
      },
    },
  );

  assert.equal(result.ok, false);
  assert.match(result.error, /被 Cloudflare 验证页拦截/);
  assert.match(result.error, /完成验证后重试/);
});

test('a successful account is not requested again during a same-day task retry', async () => {
  preferences.delete('iios_success_cache');
  const session = { label: 'account-2', token: 'stable-token', cookie: '', userAgent: 'test-agent' };
  const first = await checkinSession(session, {
    runtime: fakeRuntime({ result: { points: 42 } }),
    async request() {
      return {
        status: 200,
        headers: { 'x-signature': 'response-signature' },
        body: 'encoded-response',
      };
    },
  });
  assert.equal(first.ok, true);

  const retry = await checkinSession(session, {
    runtime: fakeRuntime({}),
    async request() { throw new Error('the successful account was requested again'); },
  });
  assert.equal(retry.ok, true);
  assert.equal(retry.cached, true);
  assert.match(retry.payload.message, /此前已签到成功/);
});

test('single-account refreshed tokens are persisted to IIOS_TOKEN', () => {
  delete process.env.IIOS_SESSIONS;
  preferences.delete('iios_token');
  const session = { label: 'single-refresh', token: 'old-token', cookie: '', userAgent: 'test-agent' };

  rememberRefreshedToken(session, 'new-token');

  assert.equal(preferences.get('iios_token'), 'new-token');
});

test('multi-account refreshed tokens are persisted to IIOS_SESSIONS', () => {
  preferences.set('iios_sessions', JSON.stringify([
    { label: 'multi-refresh-1', token: 'old-token-1', cookie: 'cookie-1', userAgent: 'test-agent-1' },
    { label: 'multi-refresh-2', token: 'old-token-2', cookie: 'cookie-2', userAgent: 'test-agent-2' },
  ]));
  const session = { label: 'multi-refresh-2', token: 'old-token-2', cookie: 'cookie-2', userAgent: 'test-agent-2' };

  rememberRefreshedToken(session, 'new-token-2');

  const persisted = JSON.parse(preferences.get('iios_sessions'));
  assert.equal(persisted[0].token, 'old-token-1');
  assert.equal(persisted[1].token, 'new-token-2');
});

test('refreshed tokens are reused by account on the next request', async () => {
  preferences.delete('iios_token_cache');
  preferences.delete('iios_success_cache');
  const session = { label: 'account-2', token: 'old-token', cookie: '', userAgent: 'test-agent' };
  rememberRefreshedToken(session, 'new-token');

  const result = await checkinSession(session, {
    runtime: fakeRuntime({ result: { points: 1 } }),
    async request(options) {
      assert.equal(options.headers.Authorization, 'Basic new-token');
      return {
        status: 200,
        headers: { 'x-signature': 'response-signature' },
        body: 'encoded-response',
      };
    },
  });

  assert.equal(result.ok, true);
});
