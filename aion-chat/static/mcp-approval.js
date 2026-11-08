/* 用户可见的 MCP 写入审批卡。服务器轮询不包含额外模型调用。 */
(function () {
  'use strict';
  const PERIOD_MS = 8000;
  let getScope = null;
  let panel = null;
  let interval = null;
  let refreshing = false;
  let currentKey = '';
  const statusText = new Map();

  function el(tag, className, textValue) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (textValue !== undefined) node.textContent = String(textValue);
    return node;
  }

  function ensurePanel() {
    if (panel) return panel;
    panel = el('section', 'mcp-approval-panel');
    panel.setAttribute('aria-label', 'MCP 写入审批');
    document.body.appendChild(panel);
    return panel;
  }

  function render(items, scope) {
    const root = ensurePanel();
    root.replaceChildren();
    for (const [key, record] of statusText) {
      if (record.until < Date.now()) statusText.delete(key);
    }
    root.hidden = !items.length && !statusText.size;
    for (const item of items) {
      const card = el('div', 'mcp-approval-card');
      card.appendChild(el('div', 'mcp-approval-title', 'MCP 写入待确认'));
      card.appendChild(el('div', 'mcp-approval-subtitle',
        (item.actor === 'connor' ? 'Connor' : '岚') + ' · ' + item.server + ' · ' + item.tool));
      card.appendChild(el('p', 'mcp-approval-note',
        '下面是将发送给 MCP 的完整参数。核对目标文件和内容后，再决定是否执行。'));
      const preview = el('pre', 'mcp-approval-preview', JSON.stringify(item.arguments, null, 2));
      card.appendChild(preview);
      const actions = el('div', 'mcp-approval-actions');
      const decline = el('button', 'mcp-approval-decline', '取消');
      const approve = el('button', 'mcp-approval-confirm', '确认执行');
      decline.type = 'button';
      approve.type = 'button';
      const result = el('div', 'mcp-approval-result', '');
      if (statusText.has(item.id)) {
        result.textContent = statusText.get(item.id).message;
      }
      async function decision(accept) {
        const active = getScope ? getScope() : null;
        if (!active || active.scope_type !== scope.scope_type || active.scope_id !== scope.scope_id) {
          result.textContent = '请回到原聊天窗口后确认这项操作。';
          return;
        }
        decline.disabled = true;
        approve.disabled = true;
        result.textContent = accept ? '正在执行，请勿重复提交…' : '正在取消…';
        try {
          const response = await fetch('/api/mcp-approvals/' + encodeURIComponent(item.id) + '/decision', {
            method: 'POST',
            cache: 'no-store',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
              nonce: item.nonce, scope_type: scope.scope_type,
              scope_id: scope.scope_id, approve: accept,
            }),
          });
          const data = await response.json();
          result.textContent = data.message || (data.detail || '请求未成功');
          statusText.set(item.id, { message: result.textContent, until: Date.now() + 30000 });
          if (response.ok && data.ok) {
            card.classList.add('mcp-approval-finished');
          }
        } catch (error) {
          result.textContent = '请求未确认完成，请检查目标服务及最新待审批列表。';
        }
      }
      decline.addEventListener('click', () => decision(false), { once: true });
      approve.addEventListener('click', () => decision(true), { once: true });
      actions.append(decline, approve);
      card.append(actions, result);
      root.appendChild(card);
    }
    const active = new Set(items.map(item => item.id));
    for (const [id, record] of statusText) {
      if (active.has(id)) continue;
      const notice = el('div', 'mcp-approval-card mcp-approval-finished');
      notice.appendChild(el('div', 'mcp-approval-title', 'MCP 操作回执'));
      notice.appendChild(el('div', 'mcp-approval-result', record.message));
      const close = el('button', 'mcp-approval-decline', '知道了');
      close.type = 'button';
      close.addEventListener('click', () => {
        statusText.delete(id);
        notice.remove();
        if (!root.children.length) root.hidden = true;
      });
      notice.appendChild(close);
      root.appendChild(notice);
    }
  }

  async function refresh() {
    if (refreshing || !getScope || document.hidden) return;
    const scope = getScope();
    const type = scope && scope.scope_type;
    const id = scope && scope.scope_id;
    if ((type !== 'private' && type !== 'chatroom') || !id) {
      if (panel) panel.hidden = true;
      return;
    }
    const key = type + ':' + id;
    refreshing = true;
    try {
      const response = await fetch('/api/mcp-approvals/pending?scope_type='
        + encodeURIComponent(type) + '&scope_id=' + encodeURIComponent(id), { cache: 'no-store' });
      if (!response.ok) return;
      const data = await response.json();
      if (getScope()?.scope_id !== id) return;
      if (currentKey !== key) {
        statusText.clear();
        currentKey = key;
      }
      render(data.pending || [], scope);
    } catch (_error) {
      // 网络异常时不操作现有审批卡，不自动批准或重试。
    } finally {
      refreshing = false;
    }
  }

  window.McpApprovalUI = {
    start(scopeGetter) {
      if (typeof scopeGetter !== 'function') return;
      getScope = scopeGetter;
      if (!interval) {
        interval = setInterval(refresh, PERIOD_MS);
        document.addEventListener('visibilitychange', () => {
          if (!document.hidden) refresh();
        });
      }
      refresh();
    },
    refresh,
  };
})();
