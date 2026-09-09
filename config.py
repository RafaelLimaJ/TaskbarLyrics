import os
import sys
import json
import time
import queue
import threading
import traceback

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_FILE = os.path.join(BASE_DIR, "position.json")
FONTS_DIR = os.path.join(BASE_DIR, "fonts")
LOG_FILE = os.path.join(BASE_DIR, "TaskbarLyrics.log")
CRASH_LOG = os.path.join(BASE_DIR, "crash.log")

CARD_WIDTH = 400
CARD_HEIGHT = 44
WS_HOST = "127.0.0.1"
WS_PORT = 5678

# ---------------------------------------------------------
# LOGGER ASSÍNCRONO EM FILA (ZERO I/O NA THREAD DE 60 FPS)
# ---------------------------------------------------------
_log_queue = queue.Queue(maxsize=2000)

def _log_worker():
    while True:
        try:
            msg = _log_queue.get()
            if msg is None:
                break
            with open(LOG_FILE, "a", encoding="utf-8") as f:
                f.write(msg)
                while not _log_queue.empty():
                    next_msg = _log_queue.get_nowait()
                    if next_msg is None:
                        return
                    f.write(next_msg)
        except Exception:
            pass

_log_thread = threading.Thread(target=_log_worker, daemon=True)
_log_thread.start()

def log_debug(msg):
    try:
        entry = f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {msg}\n"
        _log_queue.put_nowait(entry)
    except Exception:
        pass

def uncaught_exception_handler(exctype, value, tb):
    err_msg = "".join(traceback.format_exception(exctype, value, tb))
    log_debug(f"FATAL UNCAUGHT EXCEPTION:\n{err_msg}")
    try:
        with open(CRASH_LOG, "a", encoding="utf-8") as f:
            f.write(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] FATAL:\n{err_msg}\n")
    except Exception:
        pass

sys.excepthook = uncaught_exception_handler

# ---------------------------------------------------------
# PERSISTÊNCIA DA POSIÇÃO DA JANELA E CONFIGURAÇÕES
# ---------------------------------------------------------
def load_saved_position():
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                return data.get("x", 1), data.get("y", 1035)
        except Exception as e:
            log_debug(f"Erro ao carregar position.json: {e}")
    return 1, 1035

def save_position(x, y):
    try:
        data = {}
        if os.path.exists(CONFIG_FILE):
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
        data["x"] = int(x)
        data["y"] = int(y)
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f)
    except Exception as e:
        log_debug(f"Erro ao salvar position.json: {e}")

def load_translate_setting():
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                return bool(data.get("translate_to_pt", False))
        except Exception as e:
            log_debug(f"Erro ao ler translate_to_pt: {e}")
    return False

def save_translate_setting(enabled):
    try:
        data = {}
        if os.path.exists(CONFIG_FILE):
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
        data["translate_to_pt"] = bool(enabled)
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f)
    except Exception as e:
        log_debug(f"Erro ao salvar translate_to_pt: {e}")
