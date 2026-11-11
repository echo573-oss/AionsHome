'use strict';

const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const CHAT_JS = fs.readFileSync(path.join(__dirname, 'static', 'chat.js'), 'utf8');

function sourceBetween(startMarker, endMarker) {
  const start = CHAT_JS.indexOf(startMarker);
  const end = CHAT_JS.indexOf(endMarker, start);
  assert.notEqual(start, -1, `missing ${startMarker}`);
  assert.notEqual(end, -1, `missing ${endMarker}`);
  return CHAT_JS.slice(start, end);
}

test('system log copy button is distinct from the detail toggle', () => {
  const render = sourceBetween('function renderSystemLogList()', 'function toggleSysLogDetail');
  assert.match(render, /class="syslog-detail-toggle"[^>]*toggleSysLogDetail/);
  assert.match(render, /class="syslog-copy-btn"[^>]*copySystemLogEntry/);
  assert.equal(render.includes('class="syslog-detail-toggle" onclick="copySystemLogEntry'), false);
});

test('expanding a system log relabels only the detail toggle, not copy', () => {
  class FakeClassList {
    constructor(el) {
      this.el = el;
    }
    contains(name) {
      return this.el._classes.has(name);
    }
    toggle(name) {
      if (this.el._classes.has(name)) {
        this.el._classes.delete(name);
        return false;
      }
      this.el._classes.add(name);
      return true;
    }
  }

  function element(props) {
    const el = {
      className: '',
      textContent: '',
      parentElement: null,
      previousElementSibling: null,
      _classes: new Set(),
      ...props,
    };
    el.classList = new FakeClassList(el);
    if (el.className) {
      for (const name of el.className.split(/\s+/).filter(Boolean)) el._classes.add(name);
    }
    return el;
  }

  const parent = {
    children: [],
    querySelector(selector) {
      const name = selector.startsWith('.') ? selector.slice(1) : selector;
      return this.children.find(child => child._classes.has(name) || child.className.split(/\s+/).includes(name)) || null;
    },
  };
  const toggle = element({ className: 'syslog-detail-toggle', textContent: '详情 ▾', parentElement: parent });
  const copy = element({ className: 'syslog-copy-btn', textContent: '复制全文', parentElement: parent });
  const detail = element({
    id: 'sd_x',
    className: 'syslog-detail',
    parentElement: parent,
    previousElementSibling: copy,
  });
  parent.children.push(toggle, copy, detail);

  const context = {
    document: { getElementById: id => (id === 'sd_x' ? detail : null) },
  };
  vm.createContext(context);
  vm.runInContext(`${sourceBetween('function toggleSysLogDetail', 'function openSystemLog')}\nthis.toggle = toggleSysLogDetail;`, context);

  context.toggle('sd_x');
  assert.equal(toggle.textContent, '收起 ▴');
  assert.equal(copy.textContent, '复制全文');
  assert.equal(detail.classList.contains('show'), true);

  context.toggle('sd_x');
  assert.equal(toggle.textContent, '详情 ▾');
  assert.equal(copy.textContent, '复制全文');
  assert.equal(detail.classList.contains('show'), false);
});
