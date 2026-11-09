'use strict';

// 聊天室页面对齐私聊页：消息复制 + 星标 + 顶部「回到主页」按钮。
// 这里不跑整页脚本，只把消息菜单与发送者行的渲染函数抽出来单独验证。

const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const ROOT = __dirname;

function readSource() {
  return fs.readFileSync(path.join(ROOT, 'static', 'chatroom.js'), 'utf8');
}

function functionBlock(source, startMarker, endMarker) {
  const start = source.indexOf(startMarker);
  const end = source.indexOf(endMarker, start);
  assert.notEqual(start, -1, `missing ${startMarker}`);
  assert.notEqual(end, -1, `missing ${endMarker}`);
  return source.slice(start, end);
}

function loadRenderer() {
  const source = readSource();
  const rendererSource = [
    functionBlock(source, 'function crMsgMenuHtml', 'function crCanRateAiMsg'),
    functionBlock(source, 'function crMsgSenderLineHtml', 'function crEnsureMsgMenu'),
  ].join('\n');

  const context = {
    document: {readyState: 'loading', addEventListener() {}},
    crMemoryRecordMsgIds: new Set(),
    crMsgFeedbackHtml: () => '',
    esc: value => String(value ?? ''),
    timeStr: () => '16:42',
  };
  vm.createContext(context);
  vm.runInContext(rendererSource, context);
  return context;
}

function menuFor(sender, msgId, opts = {}) {
  const context = loadRenderer();
  return context.crMsgMenuHtml(sender, msgId, opts);
}

function senderLineFor(sender, msg, opts = {}) {
  const context = loadRenderer();
  return context.crMsgSenderLineHtml(sender, '测试', 'm1', msg, opts);
}

test('消息菜单提供复制入口（用户消息和 AI 消息都有）', () => {
  const userMenu = menuFor('user', 'm1', {starred: 0});
  assert.match(userMenu, /copyChatroomMsg\('m1'\)/);
  assert.match(userMenu, />复制</);

  const aiMenu = menuFor('aion', 'm1', {starred: 0});
  assert.match(aiMenu, /copyChatroomMsg\('m1'\)/);
});

test('系统消息菜单也保留复制，但不给星标入口', () => {
  const systemMenu = menuFor('system', 'm1');
  assert.match(systemMenu, /copyChatroomMsg\('m1'\)/);
  assert.doesNotMatch(systemMenu, /toggleCrStar/);
});

test('消息菜单提供星标入口，未星标显示「⭐ 星标」', () => {
  const menu = menuFor('aion', 'm1', {starred: 0});
  assert.match(menu, /toggleCrStar\('m1'\)/);
  assert.match(menu, /⭐ 星标/);
  assert.doesNotMatch(menu, /取消星标/);
});

test('已星标的消息菜单显示「取消星标」', () => {
  const menu = menuFor('aion', 'm1', {starred: 1});
  assert.match(menu, /toggleCrStar\('m1'\)/);
  assert.match(menu, /取消星标/);
  assert.doesNotMatch(menu, /⭐ 星标/);
});

test('流式消息（没有 starred 字段）不显示星标入口', () => {
  const menu = menuFor('aion', 'm1');
  assert.doesNotMatch(menu, /toggleCrStar/);
});

test('发送者行在已星标时显示 ✨ 徽章', () => {
  const starred = senderLineFor('aion', {id: 'm1', sender: 'aion', starred: 1});
  assert.match(starred, /msg-star-badge/);
  assert.match(starred, /✨/);
});

test('未星标的消息不显示徽章', () => {
  const plain = senderLineFor('aion', {id: 'm1', sender: 'aion', starred: 0});
  assert.doesNotMatch(plain, /msg-star-badge/);
});

test('用户消息的星标徽章同样出现在自己的行上', () => {
  const starred = senderLineFor('user', {id: 'm1', sender: 'user', starred: 1});
  assert.match(starred, /msg-star-badge/);
});

test('星标面板与复制函数在 chatroom.js 中定义', () => {
  const source = readSource();
  for (const name of [
    'copyChatroomMsg',
    'toggleCrStar',
    'openCrStarredPanel',
    'closeCrStarredPanel',
    'renderCrStarredList',
    'unstarFromCrPanel',
    'jumpToCrStarredMsg',
  ]) {
    assert.match(source, new RegExp(`function ${name}\\s*\\(`), `missing ${name}`);
  }
});

test('星标切换调用后端 PATCH 接口', () => {
  const source = readSource();
  assert.match(source, /`\/messages\/\$\{encodeURIComponent\(msgId\)\}\/star`/);
  assert.match(source, /method:\s*'PATCH'/);
});

test('星标列表按当前房间拉取', () => {
  const source = readSource();
  assert.match(source, /\/rooms\/\$\{encodeURIComponent\(currentRoom\.id\)\}\/starred-messages/);
});

test('聊天室顶部有「回到主页」按钮', () => {
  const html = fs.readFileSync(path.join(ROOT, 'static', 'chatroom.html'), 'utf8');
  assert.match(html, /class="home-btn"[^>]*onclick="goHome\(\)"/);
});

test('聊天室有星标面板与侧边栏入口', () => {
  const html = fs.readFileSync(path.join(ROOT, 'static', 'chatroom.html'), 'utf8');
  assert.match(html, /id="crStarredModal"/);
  assert.match(html, /id="crStarredList"/);
  assert.match(html, /openCrStarredPanel\(\)/);
});

test('样式里补齐了星标徽章、星标面板与回到主页按钮', () => {
  const css = fs.readFileSync(path.join(ROOT, 'static', 'chatroom.css'), 'utf8');
  assert.match(css, /\.msg-star-badge/);
  assert.match(css, /\.starred-item/);
  assert.match(css, /\.starred-list/);
  assert.match(css, /\.chat-header \.home-btn/);
});
