import re
import json
import time
import base64
import zlib
import threading
import unicodedata
import urllib.request
import urllib.parse
import urllib.error
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor, wait
from config import log_debug

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
DURATION_TOLERANCE = 6.0
VERSION_WORDS = ("sped up", "speed up", "slowed", "nightcore", "acoustic", "live", "remix", "instrumental",
                 "karaoke", "cover", "reverb", "8d", "tv size", "piano version", "orchestral")

_cooldown_until = {}
_cooldown_lock = threading.Lock()


# ---------------------------------------------------------
# utilidades
# ---------------------------------------------------------
def _fold(text):
    text = unicodedata.normalize("NFD", text or "")
    return "".join(c for c in text if unicodedata.category(c) != "Mn").lower()

def core_title(text):
    t = _fold(text)
    t = re.sub(r"[\(\[][^\)\]]*[\)\]]", " ", t)
    t = re.sub(r"\s[-–]\s.*$", "", t)
    return re.sub(r"[^a-z0-9]+", " ", t).strip()

def split_artists(text):
    parts = re.split(r",|&|/|、|;|\s+e\s+|\s+and\s+|\s+x\s+|\s+(?:feat|ft|with|part|com)\.?\s+", _fold(text))
    return [p.strip() for p in parts if len(p.strip()) > 1]

def accept_candidate(cand_title, cand_artists, cand_duration, query, wanted, duration):
    """Retorna a diferença de duração (>= 0) se o candidato é a mesma música; None se não for."""
    if core_title(cand_title) != core_title(query):
        return None
    full = _fold(cand_title)
    q_full = _fold(query)
    for w in VERSION_WORDS:
        if w in full and w not in q_full:
            return None
    if cand_artists and not any(w in c or c in w for w in wanted for c in cand_artists):
        return None
    diff = 0.0
    if duration > 0 and cand_duration and cand_duration > 0:
        diff = abs(cand_duration - duration)
        if diff > DURATION_TOLERANCE:
            return None
    return diff

def _http_get(url, headers=None, timeout=12, with_headers=False):
    req = urllib.request.Request(url, headers={"User-Agent": UA, **(headers or {})})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        body = resp.read()
        return (body, dict(resp.headers)) if with_headers else body

def _qs(params):
    return urllib.parse.urlencode(params)

_CREDIT_RE = re.compile(
    r"^\s*(?:lyrics?|lyricist|compos(?:er|ed|ition)|writ(?:er|ten)|produc(?:er|ed|tion)|arrang(?:er|ed|ement)|"
    r"mix(?:ed|ing)?|master(?:ed|ing)?|record(?:ed|ing)|vocals?|engineer(?:ed)?|publish(?:er|ing)|label|isrc|op|sp|"
    r"作词|作曲|编曲|制作人?|混音|母带|监制|录音|吉他|贝斯|鼓|和声|词|曲)\b[^:：]{0,24}[:：]", re.IGNORECASE)

def strip_credit_lines(lines, title, artist):
    """Remove créditos do começo (título - artista, 'Lyrics by:', '作词 :', ...) até a primeira linha de letra."""
    head = f"{core_title(title)} {core_title(artist)}".strip()
    out = []
    in_head = True
    for ln in lines:
        text = (ln.get("text") or "").strip()
        if in_head:
            folded = core_title(text)
            is_title_line = bool(folded) and (folded == core_title(title) or folded == head or
                                              folded in (f"{core_title(title)} {core_title(artist)}", f"{core_title(artist)} {core_title(title)}"))
            if not text or _CREDIT_RE.match(text) or is_title_line:
                continue
            in_head = False
        out.append(ln)
    return out


# ---------------------------------------------------------
# parsers
# ---------------------------------------------------------
def parse_krc(text):
    """KuGou: [início,dur]<offset,dur,0>palavra... (ms)."""
    lines = []
    for raw in text.splitlines():
        m = re.match(r"^\[(\d+),(\d+)\](.*)$", raw)
        if not m:
            continue
        ls, ld, body = int(m.group(1)), int(m.group(2)), m.group(3)
        words = []
        pieces = []
        for tm in re.finditer(r"<(\d+),(\d+),\d+>([^<]*)", body):
            off, dur, tok = int(tm.group(1)), int(tm.group(2)), tm.group(3)
            pieces.append(tok)
            if tok.strip():
                words.append({"text": tok, "start": ls + off, "end": ls + off + dur})
        line_text = "".join(pieces) if pieces else body
        line_text = re.sub(r"\s+", " ", line_text).strip()
        line = {"text": line_text, "start": ls, "end": ls + ld}
        if words:
            line["words"] = words
        lines.append(line)
    return lines

def parse_yrc(text):
    """NetEase: [início,dur](início,dur,0)palavra... (ms, início absoluto)."""
    lines = []
    for raw in text.splitlines():
        m = re.match(r"^\[(\d+),(\d+)\](.*)$", raw)
        if not m:
            continue
        ls, ld, body = int(m.group(1)), int(m.group(2)), m.group(3)
        marks = re.findall(r"\((\d+),(\d+),\d+\)", body)
        texts = re.split(r"\(\d+,\d+,\d+\)", body)[1:]
        words = []
        for (st, du), tok in zip(marks, texts):
            if tok.strip():
                words.append({"text": tok, "start": int(st), "end": int(st) + int(du)})
        line_text = re.sub(r"\s+", " ", "".join(texts) if texts else body).strip()
        line = {"text": line_text, "start": ls, "end": ls + ld}
        if words:
            line["words"] = words
        lines.append(line)
    return lines

def _ttml_time(value):
    if value is None:
        return None
    value = value.strip()
    if value.endswith("s"):
        value = value[:-1]
    try:
        total = 0.0
        for part in value.split(":"):
            total = total * 60 + float(part)
        return total
    except ValueError:
        return None

def _tag(elem):
    return elem.tag.rsplit("}", 1)[-1] if isinstance(elem.tag, str) else ""

def _walk_ttml(elem, parts):
    for child in list(elem):
        tag = _tag(child)
        if tag == "span":
            begin = child.get("begin")
            if begin is not None:
                parts.append((child.text or "", _ttml_time(begin), _ttml_time(child.get("end"))))
            else:
                if child.text:
                    parts.append((child.text, None, None))
                _walk_ttml(child, parts)
        elif tag == "br":
            parts.append((" ", None, None))
        if child.tail:
            parts.append((child.tail, None, None))

def parse_ttml(xml_text):
    """Apple Music TTML: <p begin end><span begin end>palavra</span> ...</p>."""
    root = ET.fromstring(xml_text)
    lines = []
    for p in root.iter():
        if _tag(p) != "p":
            continue
        parts = []
        if p.text:
            parts.append((p.text, None, None))
        _walk_ttml(p, parts)
        text = re.sub(r"\s+", " ", "".join(t for t, _, _ in parts)).strip()
        words = [{"text": t, "start": int(round(b * 1000)), "end": int(round((e if e is not None else b) * 1000))}
                 for t, b, e in parts if b is not None and t.strip()]
        pb = _ttml_time(p.get("begin"))
        pe = _ttml_time(p.get("end"))
        if pb is None and words:
            pb = words[0]["start"] / 1000.0
        if pe is None and words:
            pe = words[-1]["end"] / 1000.0
        if pb is None or pe is None or not text:
            continue
        line = {"text": text, "start": int(round(pb * 1000)), "end": int(round(pe * 1000))}
        if words:
            line["words"] = words
        lines.append(line)
    lines.sort(key=lambda l: l["start"])
    return lines


# ---------------------------------------------------------
# provedores
# ---------------------------------------------------------
_KRC_KEY = bytes([0x40, 0x47, 0x61, 0x77, 0x5e, 0x32, 0x74, 0x47, 0x51, 0x36, 0x31, 0x2d, 0xce, 0xd2, 0x6e, 0x69])

def _decode_krc(b64):
    raw = base64.b64decode(b64)[4:]
    return zlib.decompress(bytes(b ^ _KRC_KEY[i % 16] for i, b in enumerate(raw))).decode("utf-8", "ignore")

def fetch_kugou(query, wanted, duration):
    j = json.loads(_http_get("https://mobileservice.kugou.com/api/v3/search/song?" +
                             _qs({"keyword": f"{wanted[0]} {query}", "page": 1, "pagesize": 8, "format": "json"})))
    best = None
    for info in (j.get("data") or {}).get("info") or []:
        diff = accept_candidate(info.get("songname", ""), split_artists(info.get("singername", "")),
                                float(info.get("duration") or 0), query, wanted, duration)
        if diff is not None and (best is None or diff < best[0]):
            best = (diff, info)
    if not best:
        return None
    info = best[1]
    cands = json.loads(_http_get("https://lyrics.kugou.com/search?" + _qs({
        "ver": 1, "man": "yes", "client": "pc", "keyword": f"{info.get('singername', '')} - {info.get('songname', '')}",
        "duration": int(float(info.get("duration") or 0) * 1000), "hash": info["hash"]}))).get("candidates") or []
    if not cands:
        return None
    c = cands[0]
    d = json.loads(_http_get("https://lyrics.kugou.com/download?" + _qs({
        "ver": 1, "client": "pc", "id": c["id"], "accesskey": c["accesskey"], "fmt": "krc", "charset": "utf8"})))
    if not d.get("content"):
        return None
    lines = strip_credit_lines(parse_krc(_decode_krc(d["content"])), info.get("songname", query), info.get("singername", wanted[0]))
    return {"provider": "kugou_direct", "artist": info.get("singername", ""), "timed": lines} if lines else None

def fetch_netease(query, wanted, duration):
    j = json.loads(_http_get("https://music.163.com/api/search/get?" + _qs({"s": f"{query} {wanted[0]}", "type": 1, "limit": 10}),
                             headers={"Referer": "https://music.163.com/"}))
    best = None
    for s in (j.get("result") or {}).get("songs") or []:
        arts = [a.get("name", "") for a in s.get("artists", [])]
        diff = accept_candidate(s.get("name", ""), [x for a in arts for x in split_artists(a)],
                                (s.get("duration") or 0) / 1000.0, query, wanted, duration)
        if diff is not None and (best is None or diff < best[0]):
            best = (diff, s, arts)
    if not best:
        return None
    s = best[1]
    lj = json.loads(_http_get(f"https://music.163.com/api/song/lyric?id={s['id']}&lv=1&kv=1&tv=-1&yv=1",
                              headers={"Referer": "https://music.163.com/"}))
    yrc = (lj.get("yrc") or {}).get("lyric")
    if not yrc:
        return None
    lines = strip_credit_lines(parse_yrc(yrc), s.get("name", query), best[2][0] if best[2] else wanted[0])
    return {"provider": "netease_yrc", "artist": ", ".join(best[2]), "timed": lines} if lines else None

def fetch_binimum(query, wanted, duration):
    j = json.loads(_http_get("https://lyrics-api.binimum.org/?" + _qs({"track": query, "artist": wanted[0]})))
    best = None
    for r in j.get("results") or []:
        diff = accept_candidate(r.get("track_name", ""), split_artists(r.get("artist_name", "")),
                                float(r.get("duration") or 0), query, wanted, duration)
        if diff is None or not r.get("lyricsUrl"):
            continue
        rank = (0 if r.get("timing_type") == "word" else 1, diff)
        if best is None or rank < best[0]:
            best = (rank, r)
    if not best:
        return None
    r = best[1]
    lines = parse_ttml(_http_get(r["lyricsUrl"]).decode("utf-8", "ignore"))
    return {"provider": "binimum_ttml", "artist": r.get("artist_name", ""), "timed": lines} if lines else None

def fetch_lyricsplus(query, wanted, duration):
    params = {"title": query, "artist": wanted[0]}
    if 0 < duration <= 900:
        params["duration"] = int(duration)
    j = json.loads(_http_get("https://lyricsplus.binimum.org/v2/lyrics/get?" + _qs(params), timeout=20))
    meta = j.get("metadata") or {}
    if meta.get("title") and core_title(meta["title"]) != core_title(query):
        return None
    lines = []
    for ln in j.get("lyrics") or []:
        text = re.sub(r"\s+", " ", ln.get("text") or "").strip()
        if not text:
            continue
        start = int(ln.get("time") or 0)
        line = {"text": text, "start": start, "end": start + int(ln.get("duration") or 0)}
        words = [{"text": s.get("text", ""), "start": int(s.get("time") or 0), "end": int(s.get("time") or 0) + int(s.get("duration") or 0)}
                 for s in ln.get("syllabus") or [] if (s.get("text") or "").strip()]
        if words:
            line["words"] = words
        lines.append(line)
    return {"provider": "lyricsplus", "artist": ", ".join(wanted), "timed": lines} if lines else None


def fetch_betterlyrics(query, wanted, duration):
    params = {"s": query, "a": wanted[0]}
    if 0 < duration <= 900:
        params["d"] = int(duration)
    body, headers = _http_get("https://api.betterlyrics.org/getLyrics?" + _qs(params), with_headers=True, timeout=15)
    try:
        remaining = int(headers.get("x-ratelimit-remaining", headers.get("X-RateLimit-Remaining", "99")))
    except ValueError:
        remaining = 99
    if remaining <= 1:
        _block("betterlyrics", 60, "limite de requisições quase esgotado")
    ttml = (json.loads(body) or {}).get("ttml")
    if not ttml:
        return None
    lines = parse_ttml(ttml)
    return {"provider": "betterlyrics", "artist": "", "timed": lines} if lines else None


# ---------------------------------------------------------
# execução paralela com pausa por provedor
# ---------------------------------------------------------
def _block(name, seconds, why):
    with _cooldown_lock:
        _cooldown_until[name] = time.time() + seconds
    log_debug(f"[WordSync] {name} em pausa por {seconds // 60 if seconds >= 60 else seconds}{'min' if seconds >= 60 else 's'} ({why}).")

def _blocked(name):
    with _cooldown_lock:
        return time.time() < _cooldown_until.get(name, 0.0)

# espaçamento mínimo entre pedidos (segundos) para respeitar limites conhecidos
MIN_INTERVAL = {"lyricsplus": 5.5}   # LyricsPlus: 2 pedidos / 10 s
_last_call = {}
_throttle_locks = {}

def _throttle(name):
    gap = MIN_INTERVAL.get(name)
    if not gap:
        return
    lock = _throttle_locks.setdefault(name, threading.Lock())
    with lock:
        wait_s = gap - (time.time() - _last_call.get(name, 0.0))
        if wait_s > 0:
            time.sleep(min(wait_s, gap))
        _last_call[name] = time.time()

def _run_provider(name, fn, query, wanted, duration):
    if _blocked(name):
        return name, None, False
    try:
        _throttle(name)
        return name, fn(query, wanted, duration), False
    except urllib.error.HTTPError as e:
        if e.code == 429 or e.code == 403:
            wait_s = 0
            try:
                wait_s = int(e.headers.get("Retry-After", "")) if e.headers else 0
            except ValueError:
                pass
            if not wait_s:
                try:
                    m = re.search(r"wait (\d+) second", e.read().decode("utf-8", "ignore"))
                    wait_s = int(m.group(1)) + 1 if m else 0
                except Exception:
                    pass
            _block(name, max(wait_s, 5) if wait_s and wait_s < 120 else max(wait_s, 300), f"HTTP {e.code}")
        elif e.code in (401, 404):
            return name, None, False   # 401: fonte só responde músicas já em cache; 404: música não encontrada
        else:
            _block(name, 60, f"HTTP {e.code}")
        return name, None, True
    except Exception as e:
        _block(name, 60, f"{type(e).__name__}: {e}")
        return name, None, True

DIRECT_PROVIDERS = (
    ("binimum_ttml", fetch_binimum),
    ("lyricsplus", fetch_lyricsplus),
    ("betterlyrics", fetch_betterlyrics),
    ("kugou_direct", fetch_kugou),
    ("netease_yrc", fetch_netease),
)

def collect_direct_tracks(query, wanted, duration, timeout=20):
    """Consulta todas as fontes diretas em paralelo. Retorna ({provider: track}, houve_erro)."""
    tracks = {}
    had_error = False
    ex = ThreadPoolExecutor(max_workers=len(DIRECT_PROVIDERS))
    futures = [ex.submit(_run_provider, name, fn, query, wanted, duration) for name, fn in DIRECT_PROVIDERS]
    done, not_done = wait(futures, timeout=timeout)
    ex.shutdown(wait=False, cancel_futures=True)
    if not_done:
        had_error = True
    for f in done:
        name, track, err = f.result()
        had_error = had_error or err
        if track:
            tracks[name] = track
    return tracks, had_error
