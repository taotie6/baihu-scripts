'use strict';

const fs = require('node:fs');
const path = require('node:path');
const crypto = require('node:crypto');

const DEFAULT_USER_AGENT =
  'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) ' +
  'AppleWebKit/537.36 (KHTML, like Gecko) Chrome/149.0.0.0 Safari/537.36';
const STATE_FILE = process.env.IIOS_STATE_FILE || path.join(__dirname, '.iios-state.json');
const KEY_ALIASES = {
  iios_token: 'IIOS_TOKEN',
  iios_cookie: 'IIOS_COOKIE',
  iios_user_agent: 'IIOS_USER_AGENT',
  iios_sessions: 'IIOS_SESSIONS',
};

function readState() {
  try {
    return JSON.parse(fs.readFileSync(STATE_FILE, 'utf8'));
  } catch (_) {
    return {};
  }
}

function writeState(state) {
  const tempFile = `${STATE_FILE}.tmp`;
  fs.writeFileSync(tempFile, `${JSON.stringify(state, null, 2)}\n`, { mode: 0o600 });
  fs.renameSync(tempFile, STATE_FILE);
  fs.chmodSync(STATE_FILE, 0o600);
}

function envValue(key) {
  return String(process.env[KEY_ALIASES[key] || key] || '');
}

function tokenFingerprint(token) {
  return crypto.createHash('sha256').update(String(token || '')).digest('hex');
}

const state = readState();
const configuredToken = envValue('iios_token');
const configuredFingerprint = tokenFingerprint(configuredToken);
const pendingNotifications = [];

globalThis.$prefs = {
  valueForKey(key) {
    if (key === 'iios_success_cache' && state[key]) return String(state[key]);
    if (
      key === 'iios_token' &&
      state.iios_token &&
      state.token_source_fingerprint === configuredFingerprint
    ) {
      return state.iios_token;
    }
    return envValue(key);
  },

  setValueForKey(value, key) {
    try {
      state[key] = String(value || '');
      if (key === 'iios_token') state.token_source_fingerprint = configuredFingerprint;
      writeState(state);
      return true;
    } catch (error) {
      console.error(`[iios] 状态保存失败: ${error.message}`);
      return false;
    }
  },
};

globalThis.$task = {
  async fetch(options) {
    const timeoutSeconds = Number(options.timeout || 120);
    const response = await fetch(options.url, {
      method: options.method || 'GET',
      headers: options.headers || {},
      body: options.body,
      redirect: 'follow',
      signal: AbortSignal.timeout(Math.max(1, timeoutSeconds) * 1000),
    });
    return {
      status: response.status,
      statusCode: response.status,
      headers: Object.fromEntries(response.headers.entries()),
      body: await response.text(),
    };
  },
};

function firstConfiguredUserAgent() {
  try {
    const sessions = JSON.parse(envValue('iios_sessions'));
    if (Array.isArray(sessions) && sessions[0]) {
      return sessions[0].userAgent || sessions[0].ua || envValue('iios_user_agent') || DEFAULT_USER_AGENT;
    }
  } catch (_) {}
  return envValue('iios_user_agent') || DEFAULT_USER_AGENT;
}

Object.defineProperty(globalThis, 'navigator', {
  value: { userAgent: firstConfiguredUserAgent() },
  configurable: true,
  writable: true,
});
globalThis.self = globalThis;
globalThis.window = globalThis;
globalThis.location = {
  host: 'www.iios.fun',
  hostname: 'www.iios.fun',
  protocol: 'https:',
  href: 'https://www.iios.fun/points',
};
function BaihuWindow() {}
Object.defineProperty(BaihuWindow, Symbol.hasInstance, {
  value: candidate => candidate === globalThis,
});
globalThis.Window = BaihuWindow;

let baihu = null;
try {
  if (process.env.IIOS_DISABLE_NOTIFY !== '1') baihu = require('baihu');
} catch (_) {
  // Local tests do not have Baihu's injected helper package.
}

globalThis.$notify = function notify(title, subTitle, message) {
  const body = [subTitle, message].filter(Boolean).join('\n\n');
  console.log(`\n[通知] ${title}\n${body}`);
  if (/执行失败|缺少登录信息|失败[1-9]\d*/.test(`${subTitle} ${message}`)) process.exitCode = 1;
  if (baihu && typeof baihu.notify === 'function') {
    try {
      const result = baihu.notify(String(title || 'iios.fun'), body || '任务完成');
      if (result && typeof result.then === 'function') pendingNotifications.push(result);
    } catch (error) {
      console.error(`[iios] 白虎通知失败: ${error.message}`);
    }
  }
};

globalThis.$done = function done(value) {
  if (value && value.error) process.exitCode = 1;
};

process.on('beforeExit', async () => {
  if (!pendingNotifications.length) return;
  const batch = pendingNotifications.splice(0);
  await Promise.allSettled(batch);
});
