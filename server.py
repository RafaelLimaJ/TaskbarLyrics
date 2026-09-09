import json
import re
import asyncio
import threading
import websockets
from config import WS_HOST, WS_PORT, log_debug
from lyrics_engine import fetch_synced_lyrics_sync, translate_lyrics_to_pt
from ui import GLOBAL_SIGNALS

class WebSocketServerController:
    """Controlador do servidor WebSocket do TaskbarLyrics."""

    def __init__(self, state_manager, ui_widget):
        self.state = state_manager
        self.ui_widget = ui_widget
        self.current_fetch_task = None
        self.current_translate_task = None
        self.last_track_change_time = 0.0
        self.last_audio_time = 0.0
        self.last_audio_advance_time = 0.0
        self.consecutive_zero_drops = 0

    async def translate_worker(self, target_title, lyrics_copy):
        try:
            trans_map = await asyncio.to_thread(translate_lyrics_to_pt, lyrics_copy)
            if self.state.title == target_title and trans_map:
                self.state.set_translations(trans_map)
        except Exception as e:
            log_debug(f"[Server] Erro ao traduzir letras: {e}")

    async def check_lyrics_worker(self, target_title):
        delays = [0, 2, 4, 6, 10, 15]
        try:
            for delay in delays:
                if delay > 0:
                    await asyncio.sleep(delay)

                if self.state.title != target_title:
                    return
                if self.state.lyrics:
                    return

                cur_artist = self.state.artist
                cur_dur = self.state.duration

                log_debug(f"[Server] Buscando letras para '{target_title}' (Artista: '{cur_artist}', Dur: {cur_dur}s)")
                lyrics = await asyncio.to_thread(fetch_synced_lyrics_sync, target_title, cur_artist, cur_dur)

                if self.state.title != target_title:
                    return
                if self.state.lyrics:
                    return

                if lyrics:
                    self.state.set_lyrics(lyrics)
                    log_debug(f"[Server] Letras carregadas com sucesso para '{target_title}' ({len(lyrics)} versos)")

                    # Dispara tradução assíncrona em background
                    if self.current_translate_task and not self.current_translate_task.done():
                        self.current_translate_task.cancel()
                    self.current_translate_task = asyncio.create_task(self.translate_worker(target_title, list(lyrics)))
                    return
        except asyncio.CancelledError:
            pass

    async def ws_handler(self, websocket):
        log_debug("[Server] Extensão conectada via WebSocket!")

        try:
            async for message in websocket:
                data = json.loads(message)
                if data.get("type") != "YTM_UPDATE" or not data.get("isYTM", False):
                    continue

                if data.get("closed"):
                    GLOBAL_SIGNALS.stop_playback.emit()
                    continue

                raw_title = data.get("title", "")
                raw_artist = data.get("artist", "")

                title = re.sub(r"^\(\d+\)\s*", "", raw_title)
                title = re.sub(r"\s*[-|•]\s*YouTube.*$", "", title, flags=re.IGNORECASE).strip()
                artist = raw_artist.strip() if raw_artist else ""

                if not title or title.lower() in ["youtube music", "music", "youtube", "reproduzindo"]:
                    continue

                artwork_url = data.get("artworkUrl", "")
                current_time = float(data.get("currentTime", 0.0))
                duration = float(data.get("duration", 0.0))
                is_playing = bool(data.get("isPlaying", True))

                # Detecta mudança real de música
                is_different = self.state.update_track(title, artist, artwork_url, duration)
                now_loop = asyncio.get_event_loop().time()

                if is_different:
                    self.last_track_change_time = now_loop
                    self.last_audio_time = 0.0
                    self.last_audio_advance_time = now_loop
                    self.consecutive_zero_drops = 0
                    current_time = 0.0

                    if artwork_url:
                        self.ui_widget.trigger_artwork_download(artwork_url)

                    # Dispara busca de letras para a nova música
                    if self.current_fetch_task and not self.current_fetch_task.done():
                        self.current_fetch_task.cancel()
                    self.current_fetch_task = asyncio.create_task(self.check_lyrics_worker(title))
                else:
                    if artwork_url:
                        self.ui_widget.trigger_artwork_download(artwork_url)

                # Proteção robusta contra pacotes espúrios com tempo zerado no meio da faixa
                if not is_different and self.last_audio_time > 5.0 and current_time < 1.0:
                    if not is_playing:
                        # O usuário pausou! Pausa instantânea no último tempo conhecido
                        self.state.sync_time(self.last_audio_time, duration, False)
                        continue
                    else:
                        # Glitch de tempo zerado durante reprodução:
                        # Mantém a interpolação contínua fluindo suavemente e renova o heartbeat
                        effective_time = self.state.get_interpolated_time()
                        self.state.sync_time(effective_time, duration, True)
                        continue

                # Determinação robusta do estado de reprodução
                if not is_playing:
                    # Se o navegador sinalizou explicitamente pausa:
                    final_is_playing = False
                    self.last_audio_time = current_time
                else:
                    # O navegador sinalizou reprodução. Verificamos o avanço real do áudio:
                    if abs(current_time - self.last_audio_time) > 0.02:
                        self.last_audio_time = current_time
                        self.last_audio_advance_time = now_loop
                        final_is_playing = True
                    else:
                        # Tempo idêntico ao pacote anterior.
                        # Se estiver estático há mais de 0.8s, está de fato pausado (proteção contra heartbeat enganoso)
                        if self.last_audio_advance_time > 0 and (now_loop - self.last_audio_advance_time) > 0.8:
                            final_is_playing = False
                        else:
                            final_is_playing = self.state.is_playing

                if final_is_playing != self.state.is_playing:
                    log_debug(f"[Server] Estado alterado: is_playing={final_is_playing} (browser_isPlaying={is_playing}, curr={current_time:.2f}s, last={self.last_audio_time:.2f}s)")

                # Sincroniza o relógio no gerenciador de estado
                self.state.sync_time(self.last_audio_time, duration, final_is_playing)

        except Exception as e:
            log_debug(f"[Server] Conexão WebSocket encerrada: {e}")

    async def start(self):
        async with websockets.serve(self.ws_handler, WS_HOST, WS_PORT):
            log_debug(f"[Server] Servidor WebSocket escutando em ws://{WS_HOST}:{WS_PORT}")
            await asyncio.Future()

def start_server_thread(state_manager, ui_widget):
    controller = WebSocketServerController(state_manager, ui_widget)

    def run_loop():
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(controller.start())
        except Exception as e:
            log_debug(f"[Server] Erro crítico no loop do servidor: {e}")

    t = threading.Thread(target=run_loop, daemon=True)
    t.start()
    return controller
