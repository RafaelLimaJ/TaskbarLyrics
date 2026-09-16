import time
import re
import statistics
from config import log_debug


VOWELS = set("aeiouyáéíóúâêîôûãõàèìòùAEIOUYÁÉÍÓÚÂÊÎÔÛÃÕÀÈÌÒÙ")
PUNCTUATION_PAUSES = set(",;:?!-()")

CHANT_WORDS = {
    "la", "lalala", "lala", "oh", "ooh", "oooh", "ah", "aah", "yeah", "yea",
    "woah", "whoa", "na", "nanana", "uh", "uhh", "hey", "da", "dadada"
}

def is_chant_or_vocalization(text):
    if not text:
        return False
    clean = re.sub(r"[^a-zA-Z\s]", "", text).lower().strip()
    words = clean.split()
    if not words:
        return False
    return all(w in CHANT_WORDS for w in words)

def calculate_rhythm_progress(text, raw_linear_progress):
    if not text or raw_linear_progress <= 0.0:
        return 0.0
    if raw_linear_progress >= 1.0:
        return 1.0

    words = text.split()
    if not words:
        return raw_linear_progress

    # Identifica a posição de cada caractere em relação à sua palavra e à frase
    chars = list(text)
    total_len = len(chars)
    weights = []

    # Determina se a palavra é curta/átona (artigo/preposição) ou se é a última palavra (sustentação melódica)
    word_idx = 0
    in_word = False
    current_word_len = 0
    word_lengths = [len(w) for w in words]
    total_words = len(words)

    char_word_pos = []
    w_i = 0
    for c in chars:
        if c != " ":
            char_word_pos.append(w_i)
        else:
            char_word_pos.append(-1)
            w_i += 1

    for i, c in enumerate(chars):
        w_pos = char_word_pos[i] if i < len(char_word_pos) else -1
        is_last_word = (w_pos == total_words - 1)
        is_first_words = (w_pos == 0 or (w_pos == 1 and total_words >= 4))

        base_w = 1.0
        if c in PUNCTUATION_PAUSES:
            base_w = 1.5  # Pausa natural de pontuação/vírgula
        elif c in VOWELS:
            base_w = 1.2
            # Vogal na última palavra de um verso tem sustentação melódica natural
            if is_last_word:
                base_w = 1.65
        elif c == " ":
            base_w = 1.0
        else:
            base_w = 1.0

        # Palavras funcionais de introdução/ligação são faladas mais rapidamente
        if is_first_words and w_pos >= 0 and word_lengths[w_pos] <= 3:
            base_w *= 0.82

        weights.append(base_w)

    total_w = sum(weights)
    target_w = raw_linear_progress * total_w

    curr_w = 0.0
    for i, w in enumerate(weights):
        if curr_w + w >= target_w:
            sub = (target_w - curr_w) / max(0.001, w)
            return (i + sub) / total_len
        curr_w += w

    return 1.0

class PlayerStateManager:
    """Gerenciador de estado de reprodução matemática contínua e resolução de versos."""

    def __init__(self):
        self.title = ""
        self.artist = ""
        self.duration = 0.0
        self.artwork_url = ""
        self.lyrics = []
        self.translations = {}  # index -> texto em portugues
        self.translate_enabled = False
        self.median_verse_gap = 3.5

        self.last_sync_audio_time = 0.0
        self.last_sync_local_time = time.perf_counter()
        self.is_playing = False
        self.has_track = False
        self.last_heartbeat_time = time.perf_counter()

    def set_translate_enabled(self, enabled):
        self.translate_enabled = bool(enabled)
        log_debug(f"[State] Tradução para PT: {'ATIVADA' if self.translate_enabled else 'DESATIVADA'}")

    def set_translations(self, trans_map):
        self.translations = trans_map or {}
        log_debug(f"[State] {len(self.translations)} traduções vinculadas à faixa atual.")

    def update_track(self, title, artist, artwork_url, duration):
        title = title.strip()
        artist = artist.strip()

        is_different = (title != self.title or (artist and self.artist and artist != self.artist))
        self.title = title
        if artist:
            self.artist = artist
        if duration > 0:
            self.duration = duration
        if artwork_url:
            self.artwork_url = artwork_url

        self.has_track = bool(title)
        self.last_heartbeat_time = time.perf_counter()

        if is_different:
            self.lyrics = []
            self.translations = {}
            self.median_verse_gap = 3.5
            self.last_sync_audio_time = 0.0
            self.last_sync_local_time = time.perf_counter()
            self.is_playing = True
            log_debug(f"[State] Nova faixa definida: '{self.title}' - '{self.artist}' (dur={self.duration}s)")
        return is_different

    def set_lyrics(self, lyrics):
        self.lyrics = lyrics
        # Calcula a mediana do intervalo dos versos desta música específica
        if len(lyrics) >= 2:
            gaps = []
            for i in range(len(lyrics) - 1):
                g = lyrics[i + 1]["time"] - lyrics[i]["time"]
                if 0.5 <= g <= 20.0:
                    gaps.append(g)
            if gaps:
                self.median_verse_gap = float(statistics.median(gaps))
            else:
                self.median_verse_gap = 3.5
        else:
            self.median_verse_gap = 3.5
        log_debug(f"[State] {len(lyrics)} versos aplicados para '{self.title}' (Mediana de intervalo: {self.median_verse_gap:.2f}s)")


    def sync_time(self, current_time, duration, is_playing):
        now = time.perf_counter()
        if duration > 0:
            self.duration = duration
        self.is_playing = is_playing
        self.last_sync_audio_time = current_time
        self.last_sync_local_time = now
        self.last_heartbeat_time = now

    def get_interpolated_time(self):
        now = time.perf_counter()
        elapsed = (now - self.last_sync_local_time) if self.is_playing else 0.0
        return self.last_sync_audio_time + elapsed

    def is_timed_out(self):
        return (time.perf_counter() - self.last_heartbeat_time) > 10.0

    def _get_next_sung_text(self, start_idx):
        for j in range(start_idx, len(self.lyrics)):
            t = self.lyrics[j]["text"].strip()
            if t and t != "♪":
                return t
        return ""

    def get_display_state(self):
        """Retorna (line1_text, line2_text, progress, is_card_mode) para renderização a 60 FPS."""
        if not self.has_track or not self.title:
            return ("", "", 0.0, True)

        current_time = self.get_interpolated_time()

        # 1. Sem letras disponíveis -> Modo Card Limpo (Título + Artista)
        if not self.lyrics:
            return (self.title, self.artist, 1.0, True)

        # 2. Final da música ultrapassado -> Modo Card Limpo
        if self.duration > 0 and current_time >= (self.duration - 0.4):
            return (self.title, self.artist, 1.0, True)

        first_lyric_time = self.lyrics[0]["time"]

        # 3. Introdução antes do primeiro verso
        if current_time < first_lyric_time:
            remaining = first_lyric_time - current_time
            first_sung = self._get_next_sung_text(0) or self.lyrics[0]["text"]
            if remaining >= 0.4:
                prog = max(0.0, min(1.0, current_time / max(0.5, first_lyric_time)))
                return (self.title, first_sung, prog, False)
            else:
                next_v = self._get_next_sung_text(1)
                return (first_sung, next_v, 0.0, False)

        # 4. Localizar verso ativo
        active_idx = -1
        for i, item in enumerate(self.lyrics):
            if item["time"] <= current_time:
                active_idx = i
            else:
                break

        if active_idx == -1:
            first_sung = self._get_next_sung_text(0) or self.lyrics[0]["text"]
            return (first_sung, "", 0.0, False)

        start_time = self.lyrics[active_idx]["time"]
        current_text = self.lyrics[active_idx]["text"].strip()
        is_last = (active_idx + 1 >= len(self.lyrics))

        # Determina a segunda linha: tradução do verso atual (se habilitado) ou próximo verso
        secondary_text = ""
        if getattr(self, "translate_enabled", False):
            secondary_text = self.translations.get(active_idx, "")
            # Se não houver tradução específica ou for instrumental, usa fallback elegante
            if not secondary_text and current_text != "♪":
                secondary_text = self._get_next_sung_text(active_idx + 1) if not is_last else ""
        else:
            secondary_text = self._get_next_sung_text(active_idx + 1) if not is_last else ""

        if not is_last:
            next_sung = secondary_text
            end_time = self.lyrics[active_idx + 1]["time"]
            gap = max(0.1, end_time - start_time)

            # Caso 1: Verso atual já é uma marcação de instrumental (♪)
            if current_text == "♪":
                solo_prog = max(0.0, min(1.0, (current_time - start_time) / max(0.2, gap)))
                line2_display = "♪" if getattr(self, "translate_enabled", False) else self._get_next_sung_text(active_idx + 1)
                return ("♪", line2_display, solo_prog, False)

            # Caso 2: Verso cantado
            is_chant = is_chant_or_vocalization(current_text)
            words = current_text.split()
            word_count = max(1, len(words))
            char_count = len(current_text)

            # Ritmo base da música específica:
            base_tempo = max(1.5, getattr(self, "median_verse_gap", 3.5))

            # Um intervalo só é considerado solo/instrumental se for uma pausa REAL prolongada
            # (pelo menos 8 segundos de silêncio vocal E bem maior que o andamento usual da música)
            is_huge_solo_break = (gap >= max(8.0, base_tempo * 2.5))

            if is_huge_solo_break:
                # Canto com tempo natural e depois transita para solo de verdade
                text_ratio = max(0.8, min(1.6, (word_count * 0.35 + char_count * 0.04) / 3.0))
                singing_time = max(2.0, min(base_tempo * text_ratio, gap - 4.0))
                hold_time = 0.8
                instrumental_start = start_time + singing_time + hold_time
                instrumental_duration = end_time - instrumental_start

                if current_time >= instrumental_start and instrumental_duration >= 3.0:
                    solo_prog = max(0.0, min(1.0, (current_time - instrumental_start) / max(0.2, instrumental_duration)))
                    line2_display = "♪" if getattr(self, "translate_enabled", False) else self._get_next_sung_text(active_idx + 1)
                    return ("♪", line2_display, solo_prog, False)
                else:
                    time_in_verse = current_time - start_time
                    linear_prog = max(0.0, min(1.0, time_in_verse / max(0.2, singing_time)))
                    rhythm_prog = calculate_rhythm_progress(current_text, linear_prog)
                    return (current_text, next_sung, rhythm_prog, False)
            else:
                # Verso normal: estima a duração natural do canto a partir do texto
                # (palavras/caracteres) e do ritmo típico da música. Se sobrar uma pausa
                # real depois disso (ex: um trecho de instrumental antes do próximo verso),
                # essa sobra NÃO é tratada como parte do canto — a linha fica totalmente
                # preenchida e aguarda o próximo verso, em vez de "arrastar" o preenchimento
                # até o fim do intervalo (o que fazia o karaokê parecer atrasado).
                text_ratio = max(0.8, min(1.6, (word_count * 0.35 + char_count * 0.04) / 3.0))
                natural_singing = max(0.6, min(gap - 0.15, base_tempo * text_ratio))
                pause_after = gap - natural_singing

                singing_time = natural_singing if pause_after >= 1.0 else max(0.4, gap - 0.15)

                time_in_verse = current_time - start_time
                if time_in_verse >= singing_time:
                    rhythm_prog = 1.0
                else:
                    linear_prog = max(0.0, min(1.0, time_in_verse / max(0.2, singing_time)))
                    rhythm_prog = calculate_rhythm_progress(current_text, linear_prog)
                return (current_text, next_sung, rhythm_prog, False)


        else:
            # Último verso da música
            if current_text == "♪":
                time_in_verse = current_time - start_time
                if time_in_verse < 6.0:
                    prog = max(0.0, min(1.0, time_in_verse / 6.0))
                    return ("♪", self.artist or self.title, prog, False)
                else:
                    return (self.title, self.artist, 1.0, True)

            words = current_text.split()
            word_count = max(1, len(words))
            char_count = len(current_text)
            singing_time = max(1.5, word_count * 0.35 + char_count * 0.040)

            time_in_verse = current_time - start_time
            last_line2 = secondary_text if (getattr(self, "translate_enabled", False) and secondary_text) else (self.artist or self.title)
            if time_in_verse < (singing_time + 8.0):
                linear_prog = max(0.0, min(1.0, time_in_verse / max(0.2, singing_time)))
                rhythm_prog = calculate_rhythm_progress(current_text, linear_prog)
                return (current_text, last_line2, rhythm_prog, False)
            else:
                return (self.title, self.artist, 1.0, True)
