'use strict';

const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const CHATROOM_JS = fs.readFileSync(path.join(__dirname, 'static', 'chatroom.js'), 'utf8');

test('chatroom consumes a memory-library room jump query once', () => {
  assert.match(CHATROOM_JS, /consumeLaunchQueryParams\(\['room', 'msg'\]\)/);

  const start = CHATROOM_JS.indexOf('function consumeLaunchQueryParams');
  const end = CHATROOM_JS.indexOf('return consumed;', start);
  assert.notEqual(start, -1, 'missing consumeLaunchQueryParams');
  assert.notEqual(end, -1, 'missing return consumed');
  const closing = CHATROOM_JS.indexOf('}', end);
  const source = CHATROOM_JS.slice(start, closing + 1);

  let href = 'https://test.invalid/chatroom?room=room-9&msg=msg-7&keep=1';
  const history = {
    state: { x: 1 },
    replaceState(state, _title, url) {
      this.state = state;
      href = new URL(url, 'https://test.invalid').href;
    },
  };
  const context = {
    location: {
      get href() { return href; },
      get search() { return new URL(href).search; },
      get pathname() { return new URL(href).pathname; },
      get hash() { return new URL(href).hash; },
    },
    history,
    URL,
    URLSearchParams,
  };
  vm.createContext(context);
  vm.runInContext(`${source}\nthis.consumed = consumeLaunchQueryParams(['room', 'msg']);`, context);
  assert.equal(context.consumed.room, 'room-9');
  assert.equal(context.consumed.msg, 'msg-7');
  assert.equal(context.location.search, '?keep=1');
  assert.equal(context.history.state.x, 1);
});
