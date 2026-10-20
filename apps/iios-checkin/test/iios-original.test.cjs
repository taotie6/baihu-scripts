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

const { checkinSession } = require('../main/iios-original.js');

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
