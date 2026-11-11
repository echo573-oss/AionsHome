(function (root) {
  'use strict';

  // 进度心跳别太密；换歌词、播放、暂停、拖动进度立刻上报。
  var THROTTLE_MS = 12000;

  function shouldSend(slot, event, lyricKey, now) {
    if (event !== 'time') return true;
    if (lyricKey && lyricKey !== slot.lyricKey) return true;
    return now - slot.sentAt >= THROTTLE_MS;
  }

  function snapshot(audio, meta, event) {
    var current = audio && Number.isFinite(audio.currentTime) ? audio.currentTime : 0;
    var duration = audio && Number.isFinite(audio.duration) ? audio.duration : 0;
    var playing = event !== 'pause' && event !== 'stop' && !!(audio && !audio.paused && !audio.ended);
    if (event === 'play') playing = true;
    return {
      track_id: meta.trackId || '',
      external_id: meta.externalId || '',
      title: meta.title || '',
      artist: meta.artist || '',
      position_sec: current,
      duration_sec: duration > 0 ? duration : 0,
      playing: playing,
      source: meta.source || '',
      event: event || 'time',
      lyric_key: meta.lyricKey || '',
    };
  }

  function bind(audio, getMeta) {
    if (!audio || audio.dataset.musicPresenceBound === '1') return;
    audio.dataset.musicPresenceBound = '1';
    var slot = { sentAt: 0, lyricKey: '' };

    function emit(event, extra) {
      var meta = Object.assign({}, (typeof getMeta === 'function' ? getMeta() : {}) || {}, extra || {});
      var lyricKey = String(meta.lyricKey || '');
      var now = Date.now();
      if (!shouldSend(slot, event, lyricKey, now)) return;
      slot.sentAt = now;
      if (lyricKey) slot.lyricKey = lyricKey;
      var payload = snapshot(audio, meta, event);
      if (typeof fetch !== 'function') return;
      fetch('/api/music/now-playing', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
        keepalive: true,
      }).catch(function () {});
    }

    audio._aionMusicPresenceEmit = emit;
    audio.addEventListener('play', function () { emit('play'); });
    audio.addEventListener('pause', function () { emit(audio.ended ? 'stop' : 'pause'); });
    audio.addEventListener('ended', function () { emit('stop'); });
    audio.addEventListener('seeked', function () { emit('seek'); });
    audio.addEventListener('timeupdate', function () { emit('time'); });
  }

  function notifyLyric(audio, lyricKey) {
    var emit = audio && audio._aionMusicPresenceEmit;
    if (typeof emit === 'function') emit('lyric', { lyricKey: String(lyricKey) });
  }

  function stop(audio) {
    var emit = audio && audio._aionMusicPresenceEmit;
    if (typeof emit === 'function') emit('stop');
  }

  var api = { bind: bind, notifyLyric: notifyLyric, stop: stop, shouldSend: shouldSend, THROTTLE_MS: THROTTLE_MS };
  root.MusicPresence = api;
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
})(typeof globalThis !== 'undefined' ? globalThis : this);
