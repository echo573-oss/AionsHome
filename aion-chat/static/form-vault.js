/* 本机资料库 UI；不接入聊天上下文，不读取/回显任何已保存的密码。 */
(() => {
  'use strict';
  const el = (id) => document.getElementById(id);
  const status = el('status');
  let profiles = [];

  function report(message, error=false) {
    status.textContent = message;
    status.classList.toggle('error', error);
  }
  async function api(method, url, body) {
    const opts = {method, cache:'no-store', headers:{}};
    if (body !== undefined) {
      opts.headers['Content-Type'] = 'application/json';
      opts.body = JSON.stringify(body);
    }
    const response = await fetch(url, opts);
    const data = await response.json().catch(() => ({}));
    if (!response.ok || data.ok === false) throw new Error(data.detail || data.error || '操作失败');
    return data;
  }
  function button(text, action) {
    const node = document.createElement('button');
    node.type = 'button';
    node.textContent = text;
    node.addEventListener('click', action);
    return node;
  }
  function resetProfile() {
    el('profileForm').reset();
    el('profileId').value = '';
  }
  function profileFields() {
    const fields = {};
    document.querySelectorAll('#profileForm [data-field]').forEach(input => {
      const value = input.value.trim();
      if (value) fields[input.dataset.field] = value;
    });
    return fields;
  }
  function renderItem(target, record, subtitle, buttons) {
    const row = document.createElement('div');
    row.className = 'item';
    const top = document.createElement('div');
    top.className = 'top';
    const title = document.createElement('strong');
    title.textContent = record.label;
    const actions = document.createElement('div');
    actions.className = 'buttons';
    for (const entry of buttons) actions.appendChild(button(entry[0], entry[1]));
    top.append(title, actions);
    const meta = document.createElement('small');
    meta.textContent = subtitle;
    row.append(top, meta);
    target.appendChild(row);
  }
  async function load() {
    const data = await api('GET', '/api/form-vault/records');
    profiles = data.profiles || [];
    el('profiles').replaceChildren();
    el('logins').replaceChildren();
    for (const item of profiles) {
      const detail = Object.entries(item.fields).map(([k,v]) => k + ': ' + v).join(' · ');
      renderItem(el('profiles'), item, detail, [
        ['编辑', () => {
          resetProfile();
          el('profileId').value = item.id;
          el('profileLabel').value = item.label;
          document.querySelectorAll('#profileForm [data-field]').forEach(input => {
            input.value = item.fields[input.dataset.field] || '';
          });
          el('profileForm').scrollIntoView({behavior:'smooth',block:'start'});
        }],
        ['删除', () => remove('profile', item)],
      ]);
    }
    for (const item of data.logins || []) {
      renderItem(el('logins'), item, item.site + ' · ' + item.username + ' · 密码已加密', [
        ['删除', () => remove('login', item)],
      ]);
    }
  }
  async function remove(kind, item) {
    if (!confirm('确定删除「' + item.label + '」？这个操作不可撤回。')) return;
    try {
      await api('DELETE', '/api/form-vault/records/' + encodeURIComponent(kind)
        + '/' + encodeURIComponent(item.id));
      if (kind === 'profile' && el('profileId').value === item.id) resetProfile();
      await load();
      report('已删除「' + item.label + '」。');
    } catch (error) { report(error.message, true); }
  }
  el('resetProfile').addEventListener('click', resetProfile);
  el('profileForm').addEventListener('submit', async event => {
    event.preventDefault();
    const id = el('profileId').value;
    try {
      await api(id ? 'PUT' : 'POST',
        '/api/form-vault/profiles' + (id ? '/' + encodeURIComponent(id) : ''),
        {label:el('profileLabel').value,fields:profileFields()});
      resetProfile();
      await load();
      report('表单资料已加密保存。');
    } catch (error) { report(error.message, true); }
  });
  el('loginForm').addEventListener('submit', async event => {
    event.preventDefault();
    try {
      await api('POST','/api/form-vault/logins',{
        label: el('loginLabel').value,
        site: el('loginSite').value,
        username: el('loginUsername').value,
        password: el('loginPassword').value,
      });
      el('loginForm').reset();
      await load();
      report('网站凭据已加密保存，密码不会回显。');
    } catch (error) {
      el('loginPassword').value = '';
      report(error.message, true);
    }
  });
  (async () => {
    try {
      const data = await api('GET','/api/form-vault/status');
      if (!data.supported) {
        report('当前运行环境不支持 Windows DPAPI。请在本机 Windows 环境中打开此页面。',true);
        return;
      }
      el('workspace').hidden = false;
      await load();
      report('已连接 Windows 本机安全资料库。');
    } catch (error) { report(error.message, true); }
  })();
})();
