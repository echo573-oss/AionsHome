const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const test = require('node:test');

const ROOT = __dirname;
const chatSource = fs.readFileSync(path.join(ROOT, 'static', 'chat.js'), 'utf8');
const roomSource = fs.readFileSync(path.join(ROOT, 'static', 'chatroom.js'), 'utf8');

// 取出目标函数源码（括号配对，跳过字符串里的括号）
function extractFunction(source, marker) {
  const start = source.indexOf(marker);
  assert.ok(start >= 0, `未找到 ${marker}`);
  const bodyStart = source.indexOf('{', start);
  let depth = 0;
  let quote = null;
  for (let i = bodyStart; i < source.length; i++) {
    const ch = source[i];
    if (quote) {
      if (ch === '\\') { i++; continue; }
      if (ch === quote) quote = null;
      continue;
    }
    if (ch === '"' || ch === "'" || ch === '`') { quote = ch; continue; }
    if (ch === '{') depth++;
    else if (ch === '}' && --depth === 0) return source.slice(start, i + 1);
  }
  throw new Error(`函数提取失败: ${marker}`);
}

function makeEvent(overrides = {}) {
  const calls = [];
  const event = {
    key: 'Enter',
    shiftKey: false,
    ctrlKey: false,
    altKey: false,
    metaKey: false,
    isComposing: false,
    preventDefault() { calls.push('preventDefault'); },
    ...overrides,
  };
  return { event, calls };
}

const DESKTOP_UA = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0 Safari/537.36';
const ANDROID_UA = 'Mozilla/5.0 (Linux; Android 13; Pixel 7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0 Mobile Safari/537.36';
const IOS_UA = 'Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1';

function loadPrivateHandler(userAgent = DESKTOP_UA, maxTouchPoints = 0) {
  const sent = [];
  const context = {
    send: () => sent.push('send'),
    navigator: { userAgent, maxTouchPoints },
    console,
  };
  vm.createContext(context);
  vm.runInContext(extractFunction(chatSource, 'function _isMobileComposer'), context);
  vm.runInContext(extractFunction(chatSource, 'function handleKey'), context);
  return { handler: context.handleKey, sent };
}

function loadRoomHandler(userAgent = DESKTOP_UA, maxTouchPoints = 0) {
  const submitted = [];
  const context = {
    composer: { requestSubmit: () => submitted.push('submit') },
    navigator: { userAgent, maxTouchPoints },
    console,
  };
  vm.createContext(context);
  vm.runInContext(extractFunction(roomSource, 'function crIsMobileComposer'), context);
  vm.runInContext(extractFunction(roomSource, 'function crComposerKey'), context);
  return { handler: context.crComposerKey, submitted };
}

test('主私聊：Enter 直接发送', () => {
  const { handler, sent } = loadPrivateHandler();
  const { event, calls } = makeEvent();
  handler(event);
  assert.deepEqual(sent, ['send']);
  assert.deepEqual(calls, ['preventDefault']);
});

test('主私聊：Shift+Enter 换行，不发送也不拦默认行为', () => {
  const { handler, sent } = loadPrivateHandler();
  const { event, calls } = makeEvent({ shiftKey: true });
  handler(event);
  assert.deepEqual(sent, []);
  assert.deepEqual(calls, []);
});

test('主私聊：中文输入法组词中的 Enter 只选词，不发送', () => {
  const { handler, sent } = loadPrivateHandler();
  const { event, calls } = makeEvent({ isComposing: true });
  handler(event);
  assert.deepEqual(sent, []);
  assert.deepEqual(calls, []);
});

test('主私聊：Ctrl+Enter 仍然发送', () => {
  const { handler, sent } = loadPrivateHandler();
  const { event } = makeEvent({ ctrlKey: true });
  handler(event);
  assert.deepEqual(sent, ['send']);
});

test('聊天室：Enter 直接发送', () => {
  const { handler, submitted } = loadRoomHandler();
  const { event, calls } = makeEvent();
  handler(event);
  assert.deepEqual(submitted, ['submit']);
  assert.deepEqual(calls, ['preventDefault']);
});

test('聊天室：Shift+Enter 换行，不发送也不拦默认行为', () => {
  const { handler, submitted } = loadRoomHandler();
  const { event, calls } = makeEvent({ shiftKey: true });
  handler(event);
  assert.deepEqual(submitted, []);
  assert.deepEqual(calls, []);
});

test('聊天室：中文输入法组词中的 Enter 只选词，不发送', () => {
  const { handler, submitted } = loadRoomHandler();
  const { event, calls } = makeEvent({ isComposing: true });
  handler(event);
  assert.deepEqual(submitted, []);
  assert.deepEqual(calls, []);
});

test('聊天室：Ctrl+Enter 仍然发送', () => {
  const { handler, submitted } = loadRoomHandler();
  const { event } = makeEvent({ ctrlKey: true });
  handler(event);
  assert.deepEqual(submitted, ['submit']);
});

test('聊天室的发送键处理确实挂在输入框上', () => {
  assert.match(roomSource, /inputEl\.addEventListener\('keydown',\s*crComposerKey\);/);
  const privateHtml = fs.readFileSync(path.join(ROOT, 'static', 'chat.html'), 'utf8');
  assert.match(privateHtml, /id="input"[^>]*onkeydown="handleKey\(event\)"/);
});

test('手机端（Android / iOS）：回车换行，不发送也不拦默认行为', () => {
  for (const ua of [ANDROID_UA, IOS_UA]) {
    const priv = loadPrivateHandler(ua, 5);
    const privEvent = makeEvent();
    priv.handler(privEvent.event);
    assert.deepEqual(priv.sent, [], `Android/iOS 私聊不应发送：${ua}`);
    assert.deepEqual(privEvent.calls, [], `Android/iOS 私聊不应拦默认行为：${ua}`);

    const room = loadRoomHandler(ua, 5);
    const roomEvent = makeEvent();
    room.handler(roomEvent.event);
    assert.deepEqual(room.submitted, [], `Android/iOS 聊天室不应发送：${ua}`);
    assert.deepEqual(roomEvent.calls, [], `Android/iOS 聊天室不应拦默认行为：${ua}`);
  }
});

test('手机端：Shift+Enter 同样只是换行', () => {
  const priv = loadPrivateHandler(ANDROID_UA, 5);
  const privEvent = makeEvent({ shiftKey: true });
  priv.handler(privEvent.event);
  assert.deepEqual(priv.sent, []);
  assert.deepEqual(privEvent.calls, []);
});

test('iPadOS 伪装成 Macintosh 时靠触点数判定为移动端', () => {
  const ipad = loadPrivateHandler('Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15', 5);
  const event = makeEvent();
  ipad.handler(event.event);
  assert.deepEqual(ipad.sent, []);
  assert.deepEqual(event.calls, []);
});

test('桌面端（含触屏笔记本）仍然 Enter 发送', () => {
  const desktop = loadPrivateHandler(DESKTOP_UA, 10);
  const event = makeEvent();
  desktop.handler(event.event);
  assert.deepEqual(desktop.sent, ['send']);
  assert.deepEqual(event.calls, ['preventDefault']);
});
