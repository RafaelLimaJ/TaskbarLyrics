import asyncio
import datetime
from config import log_debug

# Trechos (minúsculos) do ID do app cujas sessões de mídia do Windows são aceitas.
# O Chrome fica de fora de propósito: a extensão já cuida do YouTube Music no navegador.
SMTC_APPS = ("ytm-rs",)

_PLAYING = 4   # GlobalSystemMediaTransportControlsSessionPlaybackStatus.PLAYING


class SmtcSource:
    """Lê o 'Reproduzindo agora' do Windows (SMTC) de players nativos, como o ytm-rs."""

    def __init__(self, app_patterns=SMTC_APPS):
        self.patterns = tuple(p.lower() for p in app_patterns)
        self.available = False
        self._mgr = None
        self._art_key = None

    async def start(self):
        try:
            from winrt.windows.media.control import GlobalSystemMediaTransportControlsSessionManager as Manager
            self._mgr = await Manager.request_async()
            self.available = True
            log_debug(f"[SMTC] Leitura do painel de mídia do Windows ativa para: {', '.join(self.patterns)}")
        except Exception as e:
            log_debug(f"[SMTC] Indisponível (instale 'winrt-Windows.Media.Control'): {e}")

    def _pick_session(self):
        best = None
        for s in self._mgr.get_sessions():
            app = (s.source_app_user_model_id or "").lower()
            if not any(p in app for p in self.patterns):
                continue
            if int(s.get_playback_info().playback_status) == _PLAYING:
                return s
            best = best or s
        return best

    async def read(self):
        """Retorna dict com title, artist, duration, position (já extrapolada), playing, thumbnail; ou None."""
        session = self._pick_session()
        if session is None:
            return None
        props = await session.try_get_media_properties_async()
        title = (props.title or "").strip()
        if not title:
            return None

        tl = session.get_timeline_properties()
        pb = session.get_playback_info()
        playing = int(pb.playback_status) == _PLAYING
        rate = pb.playback_rate or 1.0
        duration = max(0.0, (tl.end_time - tl.start_time).total_seconds())
        position = (tl.position - tl.start_time).total_seconds()
        if playing:
            age = (datetime.datetime.now(datetime.timezone.utc) - tl.last_updated_time).total_seconds()
            position += max(0.0, age) * rate
        if duration > 0:
            position = min(position, duration)
        return {
            "title": title,
            "artist": (props.artist or "").strip(),
            "album": (props.album_title or "").strip(),
            "duration": duration,
            "position": max(0.0, position),
            "playing": playing,
            "thumbnail": props.thumbnail,
        }

    async def read_thumbnail(self, thumbnail):
        """Bytes da capa (PNG/JPEG) ou None."""
        if thumbnail is None:
            return None
        try:
            from winrt.windows.storage.streams import DataReader
            stream = await thumbnail.open_read_async()
            size = int(stream.size)
            if size <= 0:
                return None
            reader = DataReader(stream)
            await reader.load_async(size)
            buf = bytearray(size)
            reader.read_bytes(buf)
            return bytes(buf)
        except Exception as e:
            log_debug(f"[SMTC] Não foi possível ler a capa: {e}")
            return None
