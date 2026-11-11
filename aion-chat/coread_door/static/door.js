(function () {
  const enter = document.getElementById('enter');
  const status = document.getElementById('doorStatus');
  const theme = localStorage.getItem('aion_chat_theme') || 'light';
  document.documentElement.dataset.theme = theme;
  document.body.dataset.theme = theme;

  function apply(data) {
    if (!enter) return;
    if (!data || !data.url) {
      if (status) {
        status.hidden = false;
        status.textContent = '还没配置共读室地址';
      }
      return;
    }
    enter.href = data.url;
    enter.removeAttribute('aria-disabled');
    if (!status) return;
    status.hidden = false;
    status.textContent = data.via === 'lan' ? '已连上家里的共读室' : '将从公网入口进入';
  }

  fetch('/api/coread-door', { cache: 'no-store' })
    .then((response) => (response.ok ? response.json() : null))
    .then(apply)
    .catch(() => {});
})();
