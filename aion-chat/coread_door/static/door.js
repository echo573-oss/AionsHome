(function () {
  const PUBLIC_URL = 'https://reading.ddnsto.com/';
  const enter = document.getElementById('enter');
  const status = document.getElementById('doorStatus');
  const theme = localStorage.getItem('aion_chat_theme') || 'light';
  document.documentElement.dataset.theme = theme;
  document.body.dataset.theme = theme;
  if (enter) enter.href = PUBLIC_URL;

  function apply(data) {
    if (!data || !data.url || !enter) return;
    enter.href = data.url;
    if (!status) return;
    status.hidden = false;
    status.textContent = data.via === 'lan' ? '已连上家里的共读室' : '将从公网入口进入';
  }

  fetch('/api/coread-door', { cache: 'no-store' })
    .then((response) => (response.ok ? response.json() : null))
    .then(apply)
    .catch(() => {});
})();
