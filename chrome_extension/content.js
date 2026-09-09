// TaskbarLyrics - Direct Immortal Tab WebSocket Bridge (Multi-Layer Resilient Engine)
(function () {
  if (window.location.hostname !== 'music.youtube.com') return;

  let ws = null;
  let isWsOpen = false;
  let lastSentStateJson = '';
  let lastSendTime = 0;
  let currentTrackId = '';

  function connectDirectWs() {
    if (ws && (ws.readyState === WebSocket.OPEN || ws.readyState === WebSocket.CONNECTING)) {
      return;
    }

    try {
      ws = new WebSocket('ws://127.0.0.1:5678');
      ws.onopen = () => {
        isWsOpen = true;
        lastSentStateJson = '';
        sendTrackState();
      };
      ws.onclose = () => {
        isWsOpen = false;
        setTimeout(connectDirectWs, 1500);
      };
      ws.onerror = () => {
        isWsOpen = false;
      };
    } catch (e) {
      setTimeout(connectDirectWs, 1500);
    }
  }

  function getMoviePlayer() {
    return document.getElementById('movie_player') ||
           document.querySelector('#movie_player') ||
           document.querySelector('ytmusic-player#player') ||
           document.querySelector('ytmusic-player');
  }

  function getMainVideo() {
    // Prioriza o elemento oficial de vídeo do YouTube Music player
    const main = document.querySelector('#movie_player video.html5-main-video') ||
                 document.querySelector('#movie_player video') ||
                 document.querySelector('video.video-stream.html5-main-video') ||
                 document.querySelector('video.html5-main-video');
    if (main) return main;

    const videos = Array.from(document.querySelectorAll('video'));
    if (videos.length === 0) return null;

    // 1. Vídeo ativamente tocando com áudio/tempo em andamento
    const activePlaying = videos.filter(v => !v.paused && v.currentTime > 0);
    if (activePlaying.length > 0) {
      activePlaying.sort((a, b) => (b.currentTime || 0) - (a.currentTime || 0));
      return activePlaying[0];
    }

    // 2. Vídeo com maior progresso de tempo
    const withTime = videos.filter(v => v.currentTime > 0);
    if (withTime.length > 0) {
      withTime.sort((a, b) => (b.currentTime || 0) - (a.currentTime || 0));
      return withTime[0];
    }

    return videos[0];
  }

  function getPlaybackTime(player, video) {
    // 1. YouTube Player API Oficial (o relógio interno mais confiável e exato)
    if (player && typeof player.getCurrentTime === 'function') {
      try {
        const t = player.getCurrentTime();
        if (typeof t === 'number' && !isNaN(t) && isFinite(t) && t >= 0) {
          return t;
        }
      } catch (e) {}
    }

    // 2. Vídeo ativo com tempo positivo
    if (video && typeof video.currentTime === 'number' && !isNaN(video.currentTime) && isFinite(video.currentTime) && video.currentTime > 0) {
      return video.currentTime;
    }

    // 3. Fallback: Barra de progresso DOM (aria-valuenow ou value)
    try {
      const slider = document.querySelector('ytmusic-player-bar #progress-bar') ||
                     document.querySelector('#progress-bar');
      if (slider) {
        const val = parseFloat(slider.getAttribute('aria-valuenow') || slider.value || 0);
        if (!isNaN(val) && val > 0) {
          return val;
        }
      }
    } catch (e) {}

    // 4. Fallback: Texto de tempo do DOM (#left-controls > span ou .time-info "01:01 / 03:20")
    try {
      const el = document.querySelector('#left-controls > span') ||
                 document.querySelector('ytmusic-player-bar .time-info') ||
                 document.querySelector('.time-info.ytmusic-player-bar') ||
                 document.querySelector('.time-info');
      if (el) {
        const text = (el.textContent || '').trim();
        const parts = text.split('/').map(p => p.trim());
        if (parts.length === 2 && parts[0]) {
          const segs = parts[0].split(':').map(Number);
          if (!segs.some(isNaN)) {
            const sec = (segs.length === 3) ? (segs[0] * 3600 + segs[1] * 60 + segs[2]) :
                        (segs.length === 2) ? (segs[0] * 60 + segs[1]) : (segs[0] || 0);
            if (sec > 0) return sec;
          }
        }
      }
    } catch (e) {}

    if (video && typeof video.currentTime === 'number' && !isNaN(video.currentTime)) {
      return video.currentTime;
    }
    return 0;
  }

  function getPlaybackDuration(player, video) {
    // 1. YouTube Player API Oficial
    if (player && typeof player.getDuration === 'function') {
      try {
        const d = player.getDuration();
        if (typeof d === 'number' && !isNaN(d) && isFinite(d) && d > 10 && d < 3600) {
          return d;
        }
      } catch (e) {}
    }

    // 2. Texto de tempo do DOM (#left-controls > span ou .time-info)
    try {
      const el = document.querySelector('#left-controls > span') ||
                 document.querySelector('ytmusic-player-bar .time-info') ||
                 document.querySelector('.time-info.ytmusic-player-bar') ||
                 document.querySelector('.time-info');
      if (el) {
        const text = (el.textContent || '').trim();
        const parts = text.split('/').map(p => p.trim());
        if (parts.length === 2 && parts[1]) {
          const segs = parts[1].split(':').map(Number);
          if (!segs.some(isNaN)) {
            const dur = (segs.length === 3) ? (segs[0] * 3600 + segs[1] * 60 + segs[2]) :
                        (segs.length === 2) ? (segs[0] * 60 + segs[1]) : (segs[0] || 0);
            if (dur > 10 && dur < 3600) return dur;
          }
        }
      }
    } catch (e) {}

    // 3. Slider aria-valuemax
    try {
      const slider = document.querySelector('ytmusic-player-bar #progress-bar') ||
                     document.querySelector('#progress-bar');
      if (slider) {
        const maxVal = parseFloat(slider.getAttribute('aria-valuemax') || slider.max || 0);
        if (!isNaN(maxVal) && maxVal > 10 && maxVal < 3600) return maxVal;
      }
    } catch (e) {}

    // 4. Elemento de vídeo
    if (video && !isNaN(video.duration) && isFinite(video.duration) && video.duration > 10 && video.duration < 3600) {
      return video.duration;
    }
    return 0;
  }

  function getIsPlaying(player, video) {
    // 1. Elemento de vídeo HTML5 é a autoridade máxima física de reprodução
    if (video) {
      if (video.paused || video.ended || video.playbackRate === 0) {
        return false;
      }
      if (video.readyState >= 2 && !video.paused) {
        return true;
      }
    }

    // 2. YouTube Player API Oficial (1 = Playing, 2 = Paused, 3 = Buffering, 0 = Ended)
    if (player && typeof player.getPlayerState === 'function') {
      try {
        const state = player.getPlayerState();
        if (state === 2 || state === 0 || state === -1) return false;
        if (state === 1 || state === 3) return true;
      } catch (e) {}
    }

    // 3. Botão Play/Pause da barra visual (suporte a múltiplos idiomas: EN, PT-BR, ES)
    try {
      const playPauseBtn = document.querySelector('ytmusic-player-bar #play-pause-button') ||
                           document.querySelector('#play-pause-button');
      if (playPauseBtn) {
        const label = (playPauseBtn.getAttribute('title') || playPauseBtn.getAttribute('aria-label') || '').toLowerCase();
        if (label.includes('play') || label.includes('reproduzir') || label.includes('tocar') || label.includes('iniciar')) return false;
        if (label.includes('pause') || label.includes('pausar')) return true;

        const icon = (playPauseBtn.getAttribute('icon') || '').toLowerCase();
        if (icon.includes('play')) return false;
        if (icon.includes('pause')) return true;
      }
    } catch (e) {}

    // 4. Fallback: navigator.mediaSession.playbackState
    if (navigator.mediaSession && navigator.mediaSession.playbackState) {
      if (navigator.mediaSession.playbackState === 'paused' || navigator.mediaSession.playbackState === 'none') return false;
      if (navigator.mediaSession.playbackState === 'playing') return true;
    }

    return false;
  }

  function getArtworkUrl() {
    try {
      if (navigator.mediaSession && navigator.mediaSession.metadata && navigator.mediaSession.metadata.artwork) {
        const arts = navigator.mediaSession.metadata.artwork;
        if (arts && arts.length > 0) {
          const lastArt = arts[arts.length - 1];
          if (lastArt && lastArt.src) return lastArt.src;
        }
      }

      const selectors = [
        'ytmusic-player-bar .image',
        'ytmusic-player-bar img#img',
        'ytmusic-player-bar .thumbnail img',
        'ytmusic-player-bar #thumbnail-image',
        'ytmusic-app-layout ytmusic-player-bar img',
        '#song-image img',
        'img.ytmusic-player-bar',
        '.thumbnail-image-wrapper img',
        '#thumbnail img'
      ];

      for (const sel of selectors) {
        const el = document.querySelector(sel);
        if (el) {
          const src = el.currentSrc || el.src || el.getAttribute('src');
          if (src && !src.startsWith('data:image/svg') && src.length > 5) {
            return src;
          }
        }
      }
    } catch (e) {}
    return '';
  }

  function cleanTitleString(raw) {
    if (!raw) return '';
    let cleaned = raw.replace(/^\(\d+\)\s*/, '');
    cleaned = cleaned.replace(/\s*[-|•]\s*YouTube\s*Music.*$/i, '');
    cleaned = cleaned.replace(/\s*-\s*YouTube.*$/i, '');
    return cleaned.trim();
  }

  function getTrackInfo() {
    const player = getMoviePlayer();
    const video = getMainVideo();
    let title = '';
    let artist = '';

    // 1. PRIORIDADE MÁXIMA: navigator.mediaSession.metadata (Síncrono e instantâneo na virada de música)
    if (navigator.mediaSession && navigator.mediaSession.metadata) {
      const meta = navigator.mediaSession.metadata;
      if (meta.title && meta.title.trim()) {
        title = cleanTitleString(meta.title);
      }
      if (meta.artist && meta.artist.trim()) {
        artist = meta.artist.trim();
      }
    }

    // 2. SEGUNDA VIA: Elementos DOM do player bar
    if (!title) {
      const titleEl = document.querySelector('ytmusic-player-bar .title') ||
                      document.querySelector('.title.ytmusic-player-bar') ||
                      document.querySelector('ytmusic-player-bar yt-formatted-string.title');
      if (titleEl) {
        title = cleanTitleString(titleEl.getAttribute('title') || titleEl.textContent || '');
      }
    }

    if (!artist) {
      const artistLink = document.querySelector('ytmusic-player-bar .byline a') ||
                         document.querySelector('ytmusic-player-bar .subtitle a') ||
                         document.querySelector('.byline.ytmusic-player-bar a');
      if (artistLink && artistLink.textContent.trim()) {
        artist = artistLink.textContent.trim();
      }
    }

    if (!artist) {
      const bylineEl = document.querySelector('ytmusic-player-bar .byline') ||
                       document.querySelector('.byline.ytmusic-player-bar') ||
                       document.querySelector('ytmusic-player-bar yt-formatted-string.byline') ||
                       document.querySelector('ytmusic-player-bar .subtitle');
      if (bylineEl) {
        const text = (bylineEl.getAttribute('title') || bylineEl.textContent || '').trim();
        artist = text.split(' • ')[0] || text;
      }
    }

    // 3. TERCEIRA VIA: document.title
    if (!title) {
      const rawTitle = cleanTitleString(document.title);
      if (rawTitle && rawTitle.toLowerCase() !== 'youtube music') {
        title = rawTitle;
      }
    }

    if (!title || title.toLowerCase() === 'youtube music') return null;

    const trackId = title + '___' + artist;
    if (trackId !== currentTrackId) {
      currentTrackId = trackId;
    }

    const currentTime = getPlaybackTime(player, video);
    const duration = getPlaybackDuration(player, video);
    const isPlaying = getIsPlaying(player, video);
    const artworkUrl = getArtworkUrl();

    return {
      type: 'YTM_UPDATE',
      isYTM: true,
      hostname: 'music.youtube.com',
      title: title,
      artist: artist,
      artworkUrl: artworkUrl,
      currentTime: currentTime,
      duration: duration,
      isPlaying: isPlaying
    };
  }

  function sendTrackState() {
    const info = getTrackInfo();
    if (!info || !info.title || info.title.toLowerCase() === 'youtube music') return;

    const serialized = JSON.stringify(info);
    const now = Date.now();
    // Envia se houver qualquer mudança no estado OU como heartbeat a cada 1.5s (mesmo pausado)
    if (serialized === lastSentStateJson && (now - lastSendTime) < 1500) {
      return;
    }
    lastSentStateJson = serialized;
    lastSendTime = now;

    if (ws && ws.readyState === WebSocket.OPEN) {
      try {
        ws.send(serialized);
      } catch (e) {}
    } else {
      connectDirectWs();
    }
  }

  // HOOK DO navigator.mediaSession.setActionHandler (latência zero para play/pause/seek)
  try {
    if (navigator.mediaSession && navigator.mediaSession.setActionHandler) {
      const origSetActionHandler = navigator.mediaSession.setActionHandler.bind(navigator.mediaSession);
      navigator.mediaSession.setActionHandler = function (action, handler) {
        const wrapped = function (details) {
          const res = handler ? handler(details) : undefined;
          setTimeout(sendTrackState, 0);
          setTimeout(sendTrackState, 40);
          return res;
        };
        return origSetActionHandler(action, wrapped);
      };
    }
  } catch (e) {}

  // INTERCEPTOR SÍNCRONO DO navigator.mediaSession.metadata NO MAIN WORLD
  try {
    const msProto = (window.MediaSession && MediaSession.prototype) ||
                    (navigator.mediaSession && Object.getPrototypeOf(navigator.mediaSession));
    if (msProto) {
      const desc = Object.getOwnPropertyDescriptor(msProto, 'metadata');
      if (desc && desc.set) {
        Object.defineProperty(msProto, 'metadata', {
          get: desc.get,
          set: function (val) {
            const res = desc.set.call(this, val);
            setTimeout(sendTrackState, 0);
            setTimeout(sendTrackState, 80);
            return res;
          },
          configurable: true,
          enumerable: true
        });
      }
    }
  } catch (e) {}

  // FALLBACK SE ESTIVER RODANDO EM ISOLATED WORLD (Injeta script no main world)
  try {
    if (typeof chrome !== 'undefined' && chrome.runtime && chrome.runtime.id) {
      const script = document.createElement('script');
      script.textContent = `(${function() {
        try {
          const ms = navigator.mediaSession;
          if (ms) {
            const proto = Object.getPrototypeOf(ms);
            const desc = Object.getOwnPropertyDescriptor(proto, 'metadata');
            if (desc && desc.set) {
              Object.defineProperty(proto, 'metadata', {
                get: desc.get,
                set: function(val) {
                  const r = desc.set.call(this, val);
                  window.dispatchEvent(new CustomEvent('__tbl_meta_change__'));
                  return r;
                },
                configurable: true,
                enumerable: true
              });
            }
          }
        } catch(e) {}
      }.toString()})();`;
      (document.head || document.documentElement).appendChild(script);
      script.remove();
      window.addEventListener('__tbl_meta_change__', () => {
        setTimeout(sendTrackState, 0);
        setTimeout(sendTrackState, 80);
      });
    }
  } catch (e) {}

  // RESPOSTA IMEDIATA A CLIQUES NO BOTÃO DE PLAY/PAUSE E CONTROLES
  document.addEventListener('click', (e) => {
    if (e.target && e.target.closest && e.target.closest('#play-pause-button, ytmusic-player-bar, .play-pause-button')) {
      setTimeout(sendTrackState, 0);
      setTimeout(sendTrackState, 40);
    }
  }, { capture: true, passive: true });

  // CAPTURA GLOBAL DE EVENTOS DE MÍDIA NO WINDOW E NO DOCUMENT
  const MEDIA_EVENTS = [
    'play', 'playing', 'pause', 'ended', 'timeupdate', 
    'seeked', 'seeking', 'durationchange', 'loadedmetadata', 'ratechange'
  ];
  MEDIA_EVENTS.forEach(evt => {
    window.addEventListener(evt, () => sendTrackState(), { capture: true, passive: true });
    document.addEventListener(evt, () => sendTrackState(), { capture: true, passive: true });
  });

  // MUTATION OBSERVER PARA TÍTULO E PLAYER BAR
  try {
    const titleObserver = new MutationObserver(() => sendTrackState());
    const titleEl = document.querySelector('title');
    if (titleEl) {
      titleObserver.observe(titleEl, { subtree: true, characterData: true, childList: true });
    }

    const playerObserver = new MutationObserver(() => sendTrackState());
    const tryObservePlayer = () => {
      const bar = document.querySelector('ytmusic-player-bar');
      if (bar) {
        playerObserver.observe(bar, { attributes: true, subtree: true, childList: true });
      } else {
        setTimeout(tryObservePlayer, 1000);
      }
    };
    tryObservePlayer();
  } catch (e) {}

  connectDirectWs();
  setInterval(sendTrackState, 50);
  setInterval(connectDirectWs, 3000);

  window.addEventListener('beforeunload', () => {
    if (ws && ws.readyState === WebSocket.OPEN) {
      try {
        ws.send(JSON.stringify({
          type: 'YTM_UPDATE',
          isYTM: true,
          hostname: 'music.youtube.com',
          isPlaying: false,
          closed: true
        }));
      } catch (e) {}
    }
  });
})();
