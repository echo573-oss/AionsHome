'use strict';
// 记忆库「查看原文」跳转的回归测试。
//
// 背景：记忆库在 iframe 里展示来源链接，点击后不能直接改 iframe 自身的
// 地址（那会毁掉记忆库页面），而是把消息 postMessage 给父窗口，由父窗口
// 决定跳到 /chat 还是 /chatroom 的指定消息。
//
// 这里固定住三件事：
// 1. 记忆库发出的 source-jump 链接会被转成 aion-memory-source 消息
// 2. 父窗口只接受来自自己子框架、且指向 /chat 或 /chatroom 的跳转
// 3. 缺少 conv/room 或 msg 参数时不跳转
const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const STATIC = path.join(__dirname, 'static');
const read = file => fs.readFileSync(path.join(STATIC, file), 'utf8');
const block = (s, start, end) => s.slice(s.indexOf(start), s.indexOf(end, s.indexOf(start)));

// ── 记忆库侧：点击 source-jump 时 postMessage ──────────────────────
test('memory library posts the source jump to the parent frame', () => {
  const source = read('memory-library.js');
  assert.ok(source.includes("type:'aion-memory-source'"), '应向父窗口发送 aion-memory-source 消息');
  assert.ok(source.includes("window.parent === window"), '顶层窗口时不应发送');
  assert.ok(source.includes('closest(\'a.source-jump\')'), '应只拦截 source-jump 链接');
});

// ── 父窗口侧：只接受合法跳转 ───────────────────────────────────────
function buildHost(frames) {
  const html = read('chat.html');
  // Take the whole inline script: from its opening tag to the matching close.
  const open = html.indexOf('<script id="subpage-navigation">');
  assert.ok(open > -1, 'chat.html 应包含 subpage-navigation 脚本');
  const bodyStart = html.indexOf('>', open) + 1;
  const bodyEnd = html.indexOf('</script>', bodyStart);
  const source = html.slice(bodyStart, bodyEnd);
  const assigned = [];
  const listeners = [];
  const context = {
    URL,
    location: { origin: 'http://home', assign: href => assigned.push(href) },
    document: {
      getElementById: () => ({ hidden: true, textContent: '' }),
      querySelectorAll: () => frames,
    },
    window: {
      addEventListener: (type, handler) => { if (type === 'message') listeners.push(handler); },
    },
    setTimeout, clearTimeout,
    console,
  };
  context.window.window = context.window;
  vm.createContext(context);
  vm.runInContext(source, context);
  return { assigned, listeners };
}

test('parent frame navigates to the requested conversation message', () => {
  const frame = { contentWindow: { location: { pathname: '/memory' } } };
  const { assigned, listeners } = buildHost([frame]);
  assert.equal(listeners.length, 1, '应注册一个 message 监听器');

  listeners[0]({
    origin: 'http://home',
    source: frame.contentWindow,
    data: { type: 'aion-memory-source', href: 'http://home/chat?conv=c1&msg=m1' },
  });
  assert.deepEqual(assigned, ['http://home/chat?conv=c1&msg=m1']);
});

test('parent frame ignores jumps that do not carry a message reference', () => {
  const frame = { contentWindow: { location: { pathname: '/memory' } } };
  const { assigned, listeners } = buildHost([frame]);

  // 缺少 msg 参数
  listeners[0]({
    origin: 'http://home',
    source: frame.contentWindow,
    data: { type: 'aion-memory-source', href: 'http://home/chat?conv=c1' },
  });
  // 缺少 conv/room 参数
  listeners[0]({
    origin: 'http://home',
    source: frame.contentWindow,
    data: { type: 'aion-memory-source', href: 'http://home/chat?msg=m1' },
  });
  assert.deepEqual(assigned, [], '缺少定位参数时不应跳转');
});

test('parent frame ignores messages from other origins or unknown frames', () => {
  const frame = { contentWindow: { location: { pathname: '/memory' } } };
  const { assigned, listeners } = buildHost([frame]);

  // 其它来源
  listeners[0]({
    origin: 'http://evil.example',
    source: frame.contentWindow,
    data: { type: 'aion-memory-source', href: 'http://home/chat?conv=c1&msg=m1' },
  });
  // 不是自己托管的子框架
  listeners[0]({
    origin: 'http://home',
    source: { location: { pathname: '/memory' } },
    data: { type: 'aion-memory-source', href: 'http://home/chat?conv=c1&msg=m1' },
  });
  // 其它消息类型
  listeners[0]({
    origin: 'http://home',
    source: frame.contentWindow,
    data: { type: 'something-else', href: 'http://home/chat?conv=c1&msg=m1' },
  });
  assert.deepEqual(assigned, [], '不可信的跳转一律忽略');
});

test('parent frame ignores jumps coming from a frame that left the memory page', () => {
  const frame = { contentWindow: { location: { pathname: '/kitchen' } } };
  const { assigned, listeners } = buildHost([frame]);
  listeners[0]({
    origin: 'http://home',
    source: frame.contentWindow,
    data: { type: 'aion-memory-source', href: 'http://home/chat?conv=c1&msg=m1' },
  });
  assert.deepEqual(assigned, []);
});

test('parent frame supports chatroom targets as well', () => {
  const frame = { contentWindow: { location: { pathname: '/memory' } } };
  const { assigned, listeners } = buildHost([frame]);
  listeners[0]({
    origin: 'http://home',
    source: frame.contentWindow,
    data: { type: 'aion-memory-source', href: 'http://home/chatroom?room=r1&msg=m2' },
  });
  assert.deepEqual(assigned, ['http://home/chatroom?room=r1&msg=m2']);
});
