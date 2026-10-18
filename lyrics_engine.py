import os
import re
import time
import json
import urllib.request
import urllib.parse
import urllib.error
import unicodedata
import threading
import concurrent.futures
from config import log_debug, BASE_DIR
from wordsync_sources import collect_direct_tracks

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
    title = re.sub(r"\(.*?(official|video|audio|lyric|remastered|feat|ft\.|live|acoustic|deluxe|sped up|speed up|slowed|nightcore|radio edit|single version|album version|explicit).*?\)", "", title, flags=re.IGNORECASE)
    title = re.sub(r"\[.*?(official|video|audio|lyric|remastered|feat|ft\.|live|acoustic|deluxe|sped up|speed up|slowed|nightcore|radio edit|single version|album version|explicit).*?\]", "", title, flags=re.IGNORECASE)
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
TRANSLATION_CACHE_FILE = os.path.join(BASE_DIR, "translation_cache.json")
# Dois clientes do mesmo serviço, com cota separada: se um toma 429 o outro assume
TRANSLATE_CLIENTS = ("dict-chrome-ex", "gtx")
_translate_blocked_until = {c: 0.0 for c in TRANSLATE_CLIENTS}
_translate_lock = threading.Lock()

def _line_key(text):
    return re.sub(r"[^\w\s]", "", text.lower()).strip()

def _load_translation_cache():
    try:
        with open(TRANSLATION_CACHE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            return data if isinstance(data, dict) else {}
    except Exception:
        return {}

def _save_translation_cache():
    try:
        items = list(_line_translation_cache.items())[-20000:]
        with open(TRANSLATION_CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump(dict(items), f, ensure_ascii=False)
    except Exception as e:
        log_debug(f"[Tradutor] Não foi possível salvar o cache de traduções: {e}")

_line_translation_cache = _load_translation_cache()

def _request_translation(text):
    """Traduz um bloco de linhas; tenta cada cliente que não esteja em pausa por 429."""
    last_error = None
    for client in TRANSLATE_CLIENTS:
        if time.time() < _translate_blocked_until[client]:
            continue
        try:
            url = f"https://translate.googleapis.com/translate_a/single?client={client}&sl=auto&tl=pt&dt=t&q=" + urllib.parse.quote(text)
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"})
            with urllib.request.urlopen(req, timeout=5) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            return "".join([part[0] for part in data[0] if part and part[0]])
        except urllib.error.HTTPError as e:
            last_error = e
            if e.code == 429:
                _translate_blocked_until[client] = time.time() + 300
                log_debug(f"[Tradutor] Cliente '{client}' limitado (429). Pausando por 5 min.")
        except Exception as e:
            last_error = e
    if last_error:
        raise last_error
    raise RuntimeError("todos os clientes de tradução estão em pausa")

def translate_lyrics_to_pt(lines):
    """Traduz as linhas da letra para português em lote, sem alterar os tempos originais.
    Versos que não foram traduzidos (erro/bloqueio do Google) ficam FORA do mapa, nunca com o texto original."""
    if not lines:
        return {}

    translated_map = {}
    pending = []

    for idx, item in enumerate(lines):
        txt = item["text"].strip()
        if not txt or txt == "♪" or re.match(r"^[\s\(\[\{]*(instrumental|music|música|solo|♪|♫)[\s\)\]\}]*$", txt, re.IGNORECASE):
            translated_map[idx] = ""
            continue
        cached = _line_translation_cache.get(_line_key(txt))
        if cached:
            translated_map[idx] = cached
        else:
            pending.append((idx, txt))

    # Lotes de até 50 versos por requisição (só do que ainda não está em cache), com pausa entre eles
    chunk_size = 50
    with _translate_lock:
        for start_i in range(0, len(pending), chunk_size):
            chunk = pending[start_i:start_i + chunk_size]
            try:
                translated_full = _request_translation("\n".join(txt for _, txt in chunk))
                translated_lines = translated_full.split("\n")
                if len(translated_lines) != len(chunk):
                    raise ValueError("lote de tradução desalinhado")

                for (orig_idx, orig_txt), t_line in zip(chunk, translated_lines):
                    t_line = t_line.strip()
                    translated_map[orig_idx] = t_line
                    _line_translation_cache[_line_key(orig_txt)] = t_line
                _save_translation_cache()
            except Exception as e:
                log_debug(f"[Tradutor] Lote não traduzido ({e}).")
                break
            if start_i + chunk_size < len(pending):
                time.sleep(0.8)

    log_debug(f"[Tradutor] {sum(1 for v in translated_map.values() if v)} versos traduzidos para português.")
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

def overruns_duration(parsed_lines, duration, tempo_modified=False):
    """Letra cuja última linha passa da duração da música = timestamps inválidos (ex: espaçamento sintético)."""
    if tempo_modified or duration <= 30 or not parsed_lines:
        return False
    return parsed_lines[-1]["time"] > duration + 8

# ---------------------------------------------------------
# TIMING REAL POR PALAVRA (LiriQo: Apple Music / QQ / KuGou)
# Fonte opcional e lenta (8-13s): chega depois do LRCLIB e substitui as letras
# se trouxer início/fim reais de cada palavra. Se falhar, nada muda.
# ---------------------------------------------------------
LIRIQO_URL = "https://api.liriqo-alfarrizi.my.id/v1/lyrics?"
WORDSYNC_PROVIDERS = ("binimum_ttml", "lyricsplus", "golyrics_apple_music", "amlyrics_apple_music", "betterlyrics", "qq_portato", "kugou_direct", "kugou_legato", "netease_yrc", "binimum_apple_music")
WORDSYNC_CACHE_FILE = os.path.join(BASE_DIR, "wordsync_cache.json")
WORDSYNC_POSITIVE_TTL = 90 * 86400   # letra com timing real guardada por 90 dias
WORDSYNC_NEGATIVE_TTL = 24 * 3600    # "sem timing para esta música" guardado por 1 dia
WORDSYNC_COOLDOWNS = (600, 1800, 3600, 7200)   # pausa progressiva depois de cada 429 seguido
_wordsync_lock = threading.Lock()
_wordsync_last_block_log = 0.0

def _load_wordsync_store():
    try:
        with open(WORDSYNC_CACHE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            data.setdefault("entries", {})
            return data
    except Exception:
        pass
    return {"entries": {}, "blocked_until": 0.0, "strikes": 0}

def _save_wordsync_store():
    try:
        entries = _wordsync_store["entries"]
        if len(entries) > 300:
            for k in sorted(entries, key=lambda k: entries[k]["t"])[:len(entries) - 300]:
                del entries[k]
        with open(WORDSYNC_CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump(_wordsync_store, f, ensure_ascii=False)
    except Exception as e:
        log_debug(f"[WordSync] Não foi possível salvar o cache: {e}")

def _decode_wordsync_lines(lines):
    out = []
    for l in lines:
        l = dict(l)
        if "words" in l:
            l["words"] = [tuple(p) for p in l["words"]]
        out.append(l)
    return out

_wordsync_store = _load_wordsync_store()

def artist_names(name):
    """Separa 'A e B', 'A, B', 'A & B', 'A feat. B' em nomes normalizados (colaborações)."""
    parts = re.split(r",|&|/|\s+e\s+|\s+and\s+|\s+x\s+|\s+(?:feat|ft|part|com|with)\.?\s+", normalize_text(name or "").lower())
    return [p.strip() for p in parts if len(p.strip()) > 1]

def parse_wordsync_lines(timed):
    """Converte linhas do LiriQo (ms) em {"time","text","words":[(tempo_s, pos_caractere)...]}."""
    lines = []
    for ln in timed:
        try:
            ts = float(ln["start"]) / 1000.0
            te = float(ln["end"]) / 1000.0
        except (KeyError, TypeError, ValueError):
            continue
        text = (ln.get("text") or "").strip()
        if not text or ln.get("isInstrumental"):
            lines.append({"time": ts, "text": "♪"})
            continue

        points = []
        search_from = 0
        for w in ln.get("words") or []:
            chunk = (w.get("text") or "").strip()
            if not chunk:
                continue
            idx = text.find(chunk, search_from)
            if idx == -1:
                points = []
                break
            points.append((round(float(w["start"]) / 1000.0, 3), idx))
            search_from = idx + len(chunk)

        line = {"time": ts, "text": text}
        if points:
            if points[0] != (ts, 0):
                points.insert(0, (ts, 0))
            line["words"] = points + [(max(te, points[-1][0]), len(text))]
        elif te > ts:
            # Só início/fim reais do verso (Apple Music por linha): 2 pontos
            line["words"] = [(ts, 0), (te, len(text))]
        lines.append(line)

    lines.sort(key=lambda x: x["time"])
    return lines

def _track_has_words(track):
    timed = track.get("timed") or []
    return bool(timed) and sum(1 for l in timed if l.get("words")) >= len(timed) * 0.6

def _fetch_liriqo_tracks(query, clean_artist, duration):
    """LiriQo (agregador): só consultado quando as fontes diretas não trazem timing por palavra.
    Retorna (tracks | None, houve_erro)."""
    global _wordsync_last_block_log
    now = time.time()
    with _wordsync_lock:
        blocked_until = _wordsync_store.get("blocked_until", 0.0)
    if now < blocked_until:
        if now - _wordsync_last_block_log > 60:
            _wordsync_last_block_log = now
            log_debug(f"[WordSync] LiriQo em pausa por limite de requisições; volta às {time.strftime('%H:%M', time.localtime(blocked_until))}.")
        return None, False

    try:
        params = {"title": query, "artist": clean_artist}
        if 0 < duration <= 600:
            params["duration"] = int(duration)
        req = urllib.request.Request(LIRIQO_URL + urllib.parse.urlencode(params), headers={"User-Agent": "TaskbarLyrics/3.0 (Windows 11)"})
        with urllib.request.urlopen(req, timeout=25) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body = ""
        try:
            body = e.read().decode("utf-8", "ignore")[:3000]
        except Exception:
            pass
        if e.code in (429, 403) and "1027" in body:
            # Cloudflare Workers (plano gratuito do dono): 100 mil pedidos/dia somando todos os usuários; zera 00:00 UTC
            resume = (int(time.time() // 86400) + 1) * 86400 + 120
            with _wordsync_lock:
                _wordsync_store["blocked_until"] = resume
                _save_wordsync_store()
            log_debug(f"[WordSync] LiriQo atingiu o limite diário do plano gratuito (erro 1027). Volta às {time.strftime('%H:%M', time.localtime(resume))}.")
        elif e.code in (429, 403):
            with _wordsync_lock:
                strikes = _wordsync_store.get("strikes", 0) + 1
                _wordsync_store["strikes"] = strikes
                cooldown = WORDSYNC_COOLDOWNS[min(strikes - 1, len(WORDSYNC_COOLDOWNS) - 1)]
                _wordsync_store["blocked_until"] = time.time() + cooldown
                _save_wordsync_store()
            log_debug(f"[WordSync] LiriQo limitou as requisições (HTTP {e.code}). Pausando por {cooldown // 60} min.")
        else:
            log_debug(f"[WordSync] LiriQo indisponível (HTTP {e.code}).")
        return None, True
    except Exception as e:
        log_debug(f"[WordSync] LiriQo indisponível: {e}")
        return None, True

    with _wordsync_lock:
        if _wordsync_store.get("strikes", 0):
            _wordsync_store["strikes"] = 0
            _wordsync_store["blocked_until"] = 0.0
            _save_wordsync_store()
    return {t.get("provider"): t for t in data.get("tracks", [])}, False

def fetch_wordsync_lyrics_sync(track_name, artist_name, duration, reference_lines=None):
    if not artist_name or is_tempo_modified(track_name):
        return None
    query = re.sub(r"\s*[\(\[]\s*(?:with|part\.?|feat\.?|ft\.?)\b[^)\]]*[\)\]]", "", clean_title(track_name), flags=re.IGNORECASE).strip()
    wanted = artist_names(artist_name)
    if not query or not wanted:
        return None
    clean_artist = wanted[0]

    cache_key = f"{query}___{clean_artist}___{int(duration) // 4 if duration > 0 else 0}"
    now = time.time()
    with _wordsync_lock:
        entry = _wordsync_store["entries"].get(cache_key)
        if entry:
            ttl = entry.get("ttl") or (WORDSYNC_POSITIVE_TTL if entry["lines"] else WORDSYNC_NEGATIVE_TTL)
            if now - entry["t"] < ttl:
                return _decode_wordsync_lines(entry["lines"]) if entry["lines"] else None

    # 1) fontes diretas em paralelo (Apple Music TTML, KuGou, NetEase, BetterLyrics)
    tracks, had_error = collect_direct_tracks(query, wanted, duration)
    # 2) LiriQo só se nenhuma delas trouxe timing por palavra
    if not any(_track_has_words(t) for t in tracks.values()):
        lq_tracks, lq_error = _fetch_liriqo_tracks(query, clean_artist, duration)
        for name, t in (lq_tracks or {}).items():
            tracks.setdefault(name, t)
        had_error = had_error or lq_error

    # 1ª passada: só provedores com tempo por palavra; 2ª: aceita só início/fim reais do verso
    attempts = [(p, True) for p in WORDSYNC_PROVIDERS] + [(p, False) for p in WORDSYNC_PROVIDERS]
    for provider, need_words in attempts:
        track = tracks.get(provider)
        timed = track.get("timed") if track else None
        if not timed or len(timed) < 3:
            continue
        # Algum artista da resposta precisa bater com algum do pedido (evita música homônima de outro artista)
        got = artist_names(track.get("artist", ""))
        if got and not any(w in g or g in w for w in wanted for g in got):
            continue
        # Provedor sem tempos de verdade (ex: tudo 0)
        if sum(1 for l in timed if (l.get("end") or 0) <= (l.get("start") or 0)) > len(timed) / 3:
            continue
        parsed = parse_wordsync_lines(timed)
        if is_fake_synced_lyrics(parsed) or overruns_duration(parsed, duration):
            continue
        min_points = 2 if need_words else 1
        if sum(1 for l in parsed if len(l.get("words", [])) > min_points) < len(parsed) * 0.6:
            continue
        # Sem letra de referência: a letra tem que cobrir a música (descarta homônimas bem mais curtas)
        if not reference_lines and duration > 30 and parsed[-1]["time"] < duration - 100:
            continue
        # Se já temos letra do LRCLIB, a primeira linha tem que cair perto (mesma versão da música)
        if reference_lines:
            ref_first = next((l["time"] for l in reference_lines if l["text"] != "♪"), None)
            new_first = next((l["time"] for l in parsed if l["text"] != "♪"), None)
            if ref_first is not None and new_first is not None and abs(ref_first - new_first) > 4.0:
                log_debug(f"[WordSync] {provider} descartado: início em {new_first:.1f}s vs {ref_first:.1f}s do LRCLIB")
                continue
        kind = "por palavra" if need_words else "início/fim do verso"
        log_debug(f"[WordSync] {provider}: {len(parsed)} versos com timing real ({kind}).")
        with _wordsync_lock:
            entry = {"t": time.time(), "lines": parsed}
            if not need_words:
                entry["ttl"] = 3 * 86400   # só fim de verso: tenta de novo em 3 dias (pode surgir timing por palavra)
            _wordsync_store["entries"][cache_key] = entry
            _save_wordsync_store()
        return parsed

    with _wordsync_lock:
        _wordsync_store["entries"][cache_key] = {"t": time.time(), "lines": None, "ttl": 600 if had_error else WORDSYNC_NEGATIVE_TTL}
        _save_wordsync_store()
    log_debug("[WordSync] Sem timing por palavra confiável para esta música.")
    return None

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
            with urllib.request.urlopen(req, timeout=6) as response:
                if response.status == 200:
                    data = json.loads(response.read().decode("utf-8"))
                    synced = data.get("syncedLyrics")
                    if synced:
                        parsed = parse_lrc(synced)
                        if not is_fake_synced_lyrics(parsed) and not overruns_duration(parsed, duration, is_tempo_modified(data.get("trackName", ""), data.get("albumName", ""), raw_query)):
                            item_dur = data.get("duration", 0)
                            cand_track = data.get("trackName", "")
                            cand_album = data.get("albumName", "")
                            parsed = check_and_rescale_lyrics(parsed, duration, item_dur, cand_track, raw_query, cand_album)
                            log_debug(f"[LRCLIB GET] Encontrados {len(parsed)} versos sincronizados.")
                            return parsed
        except urllib.error.HTTPError as e:
            if e.code != 404:
                log_debug(f"[LRCLIB GET] Erro HTTP {e.code}.")
        except Exception as e:
            log_debug(f"[LRCLIB GET] Falhou: {e}")

        # Fallback GET sem duração
        if clean_artist:
            try:
                params_nodur = {"track_name": query, "artist_name": clean_artist}
                url = "https://lrclib.net/api/get?" + urllib.parse.urlencode(params_nodur)
                req = urllib.request.Request(url, headers={"User-Agent": "TaskbarLyrics/3.0 (Windows 11)"})
                with urllib.request.urlopen(req, timeout=6) as response:
                    if response.status == 200:
                        data = json.loads(response.read().decode("utf-8"))
                        synced = data.get("syncedLyrics")
                        if synced:
                            parsed = parse_lrc(synced)
                            if not is_fake_synced_lyrics(parsed) and not overruns_duration(parsed, duration, is_tempo_modified(data.get("trackName", ""), data.get("albumName", ""), raw_query)):
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
            with urllib.request.urlopen(search_req, timeout=7) as search_res:
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
                        # Duração muito diferente = outra música com título parecido (versões sped up/slowed são reescaladas depois)
                        if diff > 20 and not (cand_has_tempo or query_has_tempo):
                            continue
                        if overruns_duration(parsed, duration, cand_has_tempo or query_has_tempo):
                            continue
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
