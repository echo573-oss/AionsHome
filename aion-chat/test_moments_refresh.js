'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');
const html = fs.readFileSync(path.join(__dirname, 'static/moments.html'), 'utf8');
const common = fs.readFileSync(path.join(__dirname, 'static/common.js'), 'utf8');
const block = (s, a, b) => s.slice(s.indexOf(a), s.indexOf(b, s.indexOf(a)));

test('late delivery of an older moment never pushes it above the latest post', () => {
  const ctx = vm.createContext({ allMoments: [{ id: 'new', created_at: 20 }], totalItems: 1 });
  vm.runInContext(block(html, 'function prependMomentOnce', '// ── 发布弹窗控制'), ctx);
  ctx.prependMomentOnce({ id: 'old', created_at: 2 });
  assert.equal(ctx.allMoments[0].id, 'new');
});

test('refresh requested during pagination is performed after that request', async () => {
  const pending = [];
  const ctx = vm.createContext({
    momentsLoading: false, momentsRefreshPending: false, PAGE_SIZE: 50,
    allMoments: [], momentsNextCursor: 5, momentsHasMore: true, currentPage: 1,
    URLSearchParams, console, renderList() {}, saveMomentsSnapshot() {},
    api: (method, url) => new Promise(resolve => pending.push({ url, resolve })),
  });
  vm.runInContext(block(html, 'async function loadMoments', 'function momentsSnapshotBridge'), ctx);
  const older = ctx.loadMoments(2, true);
  ctx.loadMoments(1, false);
  pending[0].resolve({ items: [{ id: 'old' }], total: 1 });
  await older;
  assert.equal(pending.length, 2);
  assert.match(pending[1].url, /page=1&/);
  pending[1].resolve({ items: [{ id: 'latest' }], total: 1 });
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(ctx.allMoments[0].id, 'latest');
});

test('snapshot reconciliation runs on connection and foreground instead of replaying history', () => {
  const events = {};
  let refreshes = 0, replays = 0, reconnect;
  const ctx = vm.createContext({
    location: { protocol: 'http:', host: 'localhost' },
    WebSocket: function() {},
    window: { addEventListener: (name, fn) => { events[name] = fn; } },
    setTimeout(fn) { reconnect = fn; },
    document: { visibilityState: 'visible', addEventListener: (name, fn) => { events[name] = fn; } },
    reconcileCommonSync() { replays++; },
  });
  vm.runInContext('const _securityAlertUi = Promise.resolve(null);\n' + block(common, 'function connectCommonWS', 'const _homePopups'), ctx);
  ctx.connectCommonWS(() => {}, { reconcile: () => { refreshes++; } });
  vm.runInContext('_commonWs.onopen()', ctx);
  events.visibilitychange();
  events.pageshow({ persisted: true });
  vm.runInContext('_commonWs.onclose()', ctx);
  reconnect();
  vm.runInContext('_commonWs.onopen()', ctx);
  assert.equal(refreshes, 4);
  assert.equal(replays, 0);
  ctx.connectCommonWS(() => {});
  vm.runInContext('_commonWs.onopen()', ctx);
  assert.equal(replays, 1, 'other pages retain event replay');
});
