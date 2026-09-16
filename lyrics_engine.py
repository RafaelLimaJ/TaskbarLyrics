import re
import json
import urllib.request
import urllib.parse
import urllib.error
import unicodedata
import threading
import concurrent.futures
from config import log_debug

def normalize_text(text):
    if not text:
        return ""
    return "".join(c for c in unicodedata.normalize("NFD", text) if unicodedata.category(c) != "Mn")

TEMPO_MODIFIERS = [
    "sped up", "speed up", "speedup", "slowed", "slowed down", "slowed reverb",
    "nightcore", "daycore", "fast version", "slow version", "pitch up"
]

def is_tempo_modified(*texts):
    combined = " ".join(normalize_text(t) for t in texts if t).lower()
    return any(m in combined for m in TEMPO_MODIFIERS)

def clean_title(title):
    if not title:
        return ""
    title = re.sub(r"^\(\d+\)\s*", "", title)
    title = re.sub(r"\(.*?(official|video|audio|lyric|remastered|feat|ft\.|live|acoustic|deluxe|sped up|speed up|slowed|nightcore).*?\)", "", title, flags=re.IGNORECASE)
    title = re.sub(r"\[.*?(official|video|audio|lyric|remastered|feat|ft\.|live|acoustic|deluxe|sped up|speed up|slowed|nightcore).*?\]", "", title, flags=re.IGNORECASE)
    title = re.sub(r"\s*[-|•]\s*YouTube.*$", "", title, flags=re.IGNORECASE)

    if " - " in title:
        parts = [p.strip() for p in title.split(" - ")]
        suffix_patterns = r"(live|ao vivo|remaster|acoustic|acustico|radio edit|remix|version|versao|audio|official|video|deluxe|bonus|sped up|speed up|speedup|slowed|nightcore|daycore)"
        if re.search(suffix_patterns, parts[-1], re.IGNORECASE):
            title = " - ".join(parts[:-1])
        elif len(parts) == 2:
            title = parts[1]
    return title.strip()

def parse_lrc(lrc_text):
    lines = []
    pattern = re.compile(r"\[(\d+):(\d+(?:\.\d+)?)\]")

    for raw_line in lrc_text.split("\n"):
        raw_line = raw_line.strip()
        if not raw_line:
            continue

        matches = list(pattern.finditer(raw_line))
        if matches:
            text = pattern.sub("", raw_line).strip()
            text = re.sub(r"<.*?>", "", text).strip()

            # Detectar linha vazia ou marcador de instrumental no LRC
            is_instrumental = False
            if not text:
                is_instrumental = True
            elif re.match(r"^[\s\(\[\{]*(instrumental|music|música|solo|interlude|♪|♫)[\s\)\]\}]*$", text, re.IGNORECASE) or text in ("//", "---", "--"):
                is_instrumental = True

            final_text = "♪" if is_instrumental else text

            for m in matches:
                minutes = int(m.group(1))
                seconds = float(m.group(2))
                total_sec = minutes * 60 + seconds
                # Ignorar tags vazias nos primeiros 1.0s (boilerplate de arquivo LRC)
                if is_instrumental and total_sec <= 1.0:
                    continue
                lines.append({"time": total_sec, "text": final_text})

    lines.sort(key=lambda x: x["time"])

    # Deduplicação inteligente de instrumentais consecutivos e mesmo timestamp
    deduped = []
    for line in lines:
        if deduped:
            prev = deduped[-1]
            if abs(prev["time"] - line["time"]) < 0.05:
                # Mesmo timestamp: prioriza texto com letra real sobre ♪
                if prev["text"] == "♪" and line["text"] != "♪":
                    deduped[-1] = line
                continue
            if prev["text"] == "♪" and line["text"] == "♪":
                continue
        deduped.append(line)

    return deduped

def is_fake_synced_lyrics(parsed_lines):
    if not parsed_lines or len(parsed_lines) < 3:
        return True
    real_text_lines = [
        line for line in parsed_lines
        if not re.match(r"^[\s\(\[\{]*(instrumental|♪|♫|music|música)[\s\)\]\}]*$", line["text"].strip(), re.IGNORECASE)
    ]
    if len(real_text_lines) < 3:
        return True
    return False

# ---------------------------------------------------------
# CACHE EM RAM (0ms)
# ---------------------------------------------------------
_lyrics_cache = {}
_translation_cache = {}

def translate_lyrics_to_pt(lines):
    """Traduz as linhas da letra para português de forma síncrona/em lote sem alterar os tempos originais."""
    if not lines:
        return {}

    # Chave de cache baseada nas primeiras linhas
    cache_key = "___".join(l["text"] for l in lines[:10])
    if cache_key in _translation_cache:
        return _translation_cache[cache_key]

    translated_map = {}
    to_translate = []
    indices = []

    for idx, item in enumerate(lines):
        txt = item["text"].strip()
        if not txt or txt == "♪" or re.match(r"^[\s\(\[\{]*(instrumental|music|música|solo|♪|♫)[\s\)\]\}]*$", txt, re.IGNORECASE):
            translated_map[idx] = ""
        else:
            indices.append(idx)
            to_translate.append(txt)

    if not to_translate:
        _translation_cache[cache_key] = translated_map
        return translated_map

    # Tradução em lotes de até 25 versos por requisição
    chunk_size = 25
    for start_i in range(0, len(to_translate), chunk_size):
        chunk_texts = to_translate[start_i:start_i + chunk_size]
        chunk_indices = indices[start_i:start_i + chunk_size]

        try:
            combined = "\n".join(chunk_texts)
            url = "https://translate.googleapis.com/translate_a/single?client=gtx&sl=auto&tl=pt&dt=t&q=" + urllib.parse.quote(combined)
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"})
            with urllib.request.urlopen(req, timeout=3.5) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                translated_full = "".join([part[0] for part in data[0] if part and part[0]])
                translated_lines = translated_full.split("\n")

                for orig_idx, t_line in zip(chunk_indices, translated_lines):
                    translated_map[orig_idx] = t_line.strip()
        except Exception as e:
            log_debug(f"[Tradutor] Erro no lote de tradução: {e}")
            for orig_idx, orig_text in zip(chunk_indices, chunk_texts):
                if orig_idx not in translated_map:
                    translated_map[orig_idx] = orig_text

    _translation_cache[cache_key] = translated_map
    log_debug(f"[Tradutor] {len(translated_map)} versos traduzidos para português.")
    return translated_map

# ---------------------------------------------------------
# CLIENTE MUSIXMATCH
# ---------------------------------------------------------
_mxm_lock = threading.Lock()
_mxm_tokens = [
    "2609029cc44f01e0c6f158cd68bee0dfca2c61ccdbc8434606aca0",
    "21051986b9886bbef9e030c27d45e9981393e68b58fc77f4461441",
    "260902dca6e138a42e53612d4fa92023b3a58e6e580e0c3d9fe239"
]
_mxm_idx = 0

def fetch_from_musixmatch(track_name, artist_name):
    global _mxm_idx
    with _mxm_lock:
        start_idx = _mxm_idx

    for i in range(len(_mxm_tokens)):
        token = _mxm_tokens[(start_idx + i) % len(_mxm_tokens)]
        try:
            q = urllib.parse.urlencode({
                "q_track": track_name,
                "q_artist": artist_name,
                "usertoken": token,
                "app_id": "web-desktop-app-v1.0"
            })
            url = "https://apic-desktop.musixmatch.com/ws/1.1/matcher.subtitle.get?" + q
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"})
            with urllib.request.urlopen(req, timeout=2.0) as resp:
                data = json.loads(resp.read().decode())
                sub_body = data.get("message", {}).get("body", {}).get("subtitle", {}).get("subtitle_body", "")
                if sub_body:
                    parsed = parse_lrc(sub_body)
                    if not is_fake_synced_lyrics(parsed):
                        log_debug(f"[MXM] Encontrados {len(parsed)} versos sincronizados.")
                        return parsed
        except Exception:
            with _mxm_lock:
                _mxm_idx = (_mxm_idx + 1) % len(_mxm_tokens)
    return None

# ---------------------------------------------------------
# ---------------------------------------------------------
# AJUSTE INTELIGENTE DE TEMPO (SPED UP / SLOWED / NIGHTCORE)
# ---------------------------------------------------------
def check_and_rescale_lyrics(parsed_lines, user_duration, item_duration, cand_title="", raw_query="", cand_album=""):
    if not parsed_lines or user_duration <= 30 or item_duration <= 30:
        return parsed_lines

    cand_has_tempo = is_tempo_modified(cand_title, cand_album)
    query_has_tempo = is_tempo_modified(raw_query)
    tempo_mismatch = (cand_has_tempo != query_has_tempo)

    ratio = float(user_duration) / float(item_duration)

    # Auto-escalonar se houver mismatch de modificador de tempo (ex: música normal e letra Sped Up)
    # ou se ambos possuem modificador com durações divergentes
    if (tempo_mismatch or (cand_has_tempo and query_has_tempo)) and (0.65 <= ratio <= 1.45) and abs(ratio - 1.0) >= 0.015:
        log_debug(f"[LyricsEngine] Versão com tempo modificado detectada ('{cand_title}'). Auto-escalando timestamps por {ratio:.4f}x ({item_duration:.1f}s -> {user_duration:.1f}s).")
        scaled = []
        for line in parsed_lines:
            scaled.append({
                "time": round(line["time"] * ratio, 2),
                "text": line["text"]
            })
        return scaled
    return parsed_lines

# ---------------------------------------------------------
# MOTOR DE BUSCA CONCORRENTE PARALELO
# ---------------------------------------------------------
def fetch_synced_lyrics_sync(track_name, artist_name="", duration=0):
    raw_query = track_name
    query = clean_title(track_name)
    clean_artist = normalize_text(artist_name).split(",")[0].strip()
    if clean_artist.lower() in ["desconhecido", "unknown"]:
        clean_artist = ""

    if not clean_artist and " - " in track_name:
        parts = track_name.split(" - ")
        if len(parts) >= 2:
            clean_artist = normalize_text(parts[0]).strip()
            query = clean_title(" - ".join(parts[1:]))

    if not query or query.lower() in ["youtube music", "music", "youtube", "reproduzindo"]:
        return []

    dur_bucket = int(duration) if duration > 0 else 0
    cache_key = f"{query}___{clean_artist}___{dur_bucket}"
    if cache_key in _lyrics_cache:
        cached = _lyrics_cache[cache_key]
        if cached:
            log_debug(f"[Cache RAM] Letras recuperadas instantaneamente para '{query}' ({len(cached)} versos)")
            return cached

    log_debug(f"[LyricsEngine] Buscando letras para: '{query}' (Artista: '{clean_artist}', Dur: {duration}s)")

    def worker_lrclib_get():
        try:
            params = {"track_name": query}
            if clean_artist:
                params["artist_name"] = clean_artist
            if 0 < duration <= 600:
                params["duration"] = int(duration)

            url = "https://lrclib.net/api/get?" + urllib.parse.urlencode(params)
            req = urllib.request.Request(url, headers={"User-Agent": "TaskbarLyrics/3.0 (Windows 11)"})
            with urllib.request.urlopen(req, timeout=2.5) as response:
                if response.status == 200:
                    data = json.loads(response.read().decode("utf-8"))
                    synced = data.get("syncedLyrics")
                    if synced:
                        parsed = parse_lrc(synced)
                        if not is_fake_synced_lyrics(parsed):
                            item_dur = data.get("duration", 0)
                            cand_track = data.get("trackName", "")
                            cand_album = data.get("albumName", "")
                            parsed = check_and_rescale_lyrics(parsed, duration, item_dur, cand_track, raw_query, cand_album)
                            log_debug(f"[LRCLIB GET] Encontrados {len(parsed)} versos sincronizados.")
                            return parsed
        except Exception:
            pass

        # Fallback GET sem duração
        if clean_artist:
            try:
                params_nodur = {"track_name": query, "artist_name": clean_artist}
                url = "https://lrclib.net/api/get?" + urllib.parse.urlencode(params_nodur)
                req = urllib.request.Request(url, headers={"User-Agent": "TaskbarLyrics/3.0 (Windows 11)"})
                with urllib.request.urlopen(req, timeout=2.5) as response:
                    if response.status == 200:
                        data = json.loads(response.read().decode("utf-8"))
                        synced = data.get("syncedLyrics")
                        if synced:
                            parsed = parse_lrc(synced)
                            if not is_fake_synced_lyrics(parsed):
                                item_dur = data.get("duration", 0)
                                cand_track = data.get("trackName", "")
                                cand_album = data.get("albumName", "")
                                parsed = check_and_rescale_lyrics(parsed, duration, item_dur, cand_track, raw_query, cand_album)
                                log_debug(f"[LRCLIB GET (sem dur)] Encontrados {len(parsed)} versos.")
                                return parsed
            except Exception:
                pass
        return None

    def worker_lrclib_search():
        try:
            search_q = f"{query} {clean_artist}".strip()
            search_url = "https://lrclib.net/api/search?" + urllib.parse.urlencode({"q": search_q})
            search_req = urllib.request.Request(search_url, headers={"User-Agent": "TaskbarLyrics/3.0 (Windows 11)"})
            with urllib.request.urlopen(search_req, timeout=3.0) as search_res:
                if search_res.status == 200:
                    results = json.loads(search_res.read().decode("utf-8"))
                    norm_art = normalize_text(clean_artist).lower() if clean_artist else ""
                    norm_query = normalize_text(query).lower()
                    query_has_tempo = is_tempo_modified(raw_query)
                    candidates = []
                    for item in results:
                        synced = item.get("syncedLyrics")
                        if not synced:
                            continue
                        item_track = normalize_text(item.get("trackName", "")).lower()
                        if norm_query not in item_track and item_track not in norm_query:
                            query_words = set(norm_query.split())
                            track_words = set(item_track.split())
                            if not query_words or len(query_words & track_words) / len(query_words) < 0.6:
                                continue
                        item_art = normalize_text(item.get("artistName", "")).lower()
                        if norm_art and len(norm_art) > 2:
                            if norm_art not in item_art and item_art not in norm_art:
                                continue
                        parsed = parse_lrc(synced)
                        if not parsed or is_fake_synced_lyrics(parsed):
                            continue

                        cand_album = item.get("albumName", "")
                        cand_has_tempo = is_tempo_modified(item.get("trackName", ""), cand_album)
                        tempo_mismatch = 1 if (cand_has_tempo != query_has_tempo) else 0

                        item_dur = item.get("duration", 0)
                        diff = abs(item_dur - duration) if duration > 0 and item_dur else 0
                        has_intro = 1 if parsed[0]["time"] > 1.0 else 0
                        is_video = 1 if "video" in item_track else 0
                        candidates.append((tempo_mismatch, diff, is_video, -has_intro, parsed, item))

                    if candidates:
                        candidates.sort(key=lambda x: (x[0], x[1], x[2], x[3]))
                        best_candidate = candidates[0][4]
                        best_item = candidates[0][5]
                        item_dur = best_item.get("duration", 0)
                        cand_track = best_item.get("trackName", "")
                        cand_album = best_item.get("albumName", "")
                        best_candidate = check_and_rescale_lyrics(best_candidate, duration, item_dur, cand_track, raw_query, cand_album)
                        log_debug(f"[LRCLIB SEARCH] Selecionada versao ({len(best_candidate)} versos, intro={best_candidate[0]['time']}s) para {best_item.get('trackName')} - {best_item.get('artistName')}")
                        return best_candidate
        except urllib.error.HTTPError as e:
            if e.code == 503:
                log_debug("[Rede/API] LRCLIB SEARCH com lentidão/503 (Serviço temporariamente sobrecarregado). Tentando outras fontes...")
            else:
                log_debug(f"[Rede/API] LRCLIB SEARCH erro HTTP {e.code}: {e.reason}")
        except urllib.error.URLError as e:
            log_debug(f"[Internet/Rede] Falha de conexão com a internet ou timeout ao buscar letras: {e.reason}")
        except Exception as e:
            log_debug(f"[LyricsEngine] Erro no LRCLIB SEARCH: {e}")
        return None

    def worker_musixmatch():
        if clean_artist:
            try:
                return fetch_from_musixmatch(query, clean_artist)
            except Exception:
                pass
        return None

    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as executor:
        futures = {
            executor.submit(worker_lrclib_get): "GET",
            executor.submit(worker_lrclib_search): "SEARCH",
            executor.submit(worker_musixmatch): "MXM"
        }
        for future in concurrent.futures.as_completed(futures):
            res = future.result()
            if res and len(res) >= 3:
                _lyrics_cache[cache_key] = res
                return res

    return []
